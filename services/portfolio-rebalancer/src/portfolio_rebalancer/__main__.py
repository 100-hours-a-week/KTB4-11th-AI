import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
import sqlalchemy as sa
from ktb_core.logging import StructuredLogger, get_logger, setup_logging, start_logging

from portfolio_rebalancer.backend import Backend
from portfolio_rebalancer.holidays import COVERED_THROUGH, KST, hours_left, in_session
from portfolio_rebalancer.market import daily_closes, latest_prices
from portfolio_rebalancer.portfolio import Unready, load_portfolio
from portfolio_rebalancer.rebalance import quote, rebalance
from portfolio_rebalancer.settings import Settings
from portfolio_rebalancer.snapshot import Account

TOTALS = (
    "cancelled",
    "cancel_failed",
    "sent",
    "failed",
    "limit",
    "market",
    "upper_triggered",
    "lower_triggered",
)


def _failure(error: httpx.HTTPError) -> dict[str, Any]:
    if isinstance(error, httpx.HTTPStatusError):
        return {"status": error.response.status_code, "body": error.response.text}
    return {"error": str(error)}


def _cancel_all(
    backend: Backend, log: StructuredLogger, user_id: int, account: Account, counts: Counter[str]
) -> bool:
    for pending in account.pending_orders:
        fields = {
            "user_id": user_id,
            "account_id": account.account_id,
            "stock_code": pending.stock_code,
            "side": pending.order_side,
            "order_id": pending.order_id,
        }
        try:
            backend.cancel(user_id, account.account_id, pending.order_id)
        except httpx.HTTPError as error:
            counts["cancel_failed"] += 1
            log.error("cancel_failed", **fields, **_failure(error))
            return False
        counts["cancelled"] += 1
        log.info(
            "order_cancelled",
            **fields,
            order_type=pending.order_type,
            limit_price=pending.limit_price,
            quantity=pending.quantity,
        )
    return True


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-rebalancer")
    log = get_logger(__name__, run_id=str(uuid.uuid4()))
    with start_logging(log, band=settings.band, buy_buffer=settings.buy_buffer) as end:
        now = datetime.now(UTC)
        days_left = (COVERED_THROUGH - now.astimezone(KST).date()).days
        if days_left < 30:
            log.warning(
                "holidays_expiring",
                covered_through=COVERED_THROUGH.isoformat(),
                days_left=days_left,
            )
        runs_left, week_runs = hours_left(now)
        end.update(runs_left=runs_left, week_runs=week_runs, last_run=runs_left == 1)
        if not settings.test_mode and not in_session(now):
            end.update(outcome="market_closed")
            raise SystemExit(0)

        engine = sa.create_engine(settings.postgres_dsn)
        try:
            portfolio = load_portfolio(engine)
        finally:
            engine.dispose()
        if portfolio is None:
            end.update(outcome="no_portfolio")
            raise SystemExit(0)
        if isinstance(portfolio, Unready):
            end.update(outcome=portfolio.status, portfolio_id=portfolio.id)
            raise SystemExit(1 if portfolio.status == "explanation_failed" else 0)

        counts: Counter[str] = Counter()
        with httpx.Client(base_url=settings.backend_url, timeout=10.0) as client:
            backend = Backend(
                client,
                settings.backend_jwt_secret.get_secret_value(),
                settings.backend_jwt_issuer,
            )
            users = backend.users()
            log.info("users_received", user_count=len(users))
            failed_cancels: set[int] = set()
            for user in users:
                for account in user.accounts:
                    if account.is_active and not _cancel_all(
                        backend, log, user.user_id, account, counts
                    ):
                        failed_cancels.add(account.account_id)
            if counts["cancelled"]:
                users = backend.users()
                log.info("users_received", user_count=len(users))
            codes = {t.stock_code for t in portfolio.targets} | {
                s.stock_code for u in users for a in u.accounts for s in a.stocks
            }
            closes = daily_closes(settings.questdb_conf, codes, now)
            prices = latest_prices(settings.questdb_conf, codes)
            if missing := sorted(codes - closes.keys()):
                log.warning("no_price", stock_codes=missing, missing="daily_closes")
            if missing := sorted(codes - prices.keys()):
                log.warning("no_price", stock_codes=missing, missing="latest_price")
            quotes = {c: quote(closes[c], prices[c]) for c in closes.keys() & prices.keys()}
            named = {t.stock_code for t in portfolio.targets}
            for user in users:
                for account in user.accounts:
                    if not account.is_active or account.account_id in failed_cancels:
                        continue
                    if account.pending_orders:
                        log.warning(
                            "pending_after_cancel",
                            user_id=user.user_id,
                            account_id=account.account_id,
                            order_ids=[o.order_id for o in account.pending_orders],
                        )
                        continue
                    if stranded := sorted(
                        {s.stock_code for s in account.stocks if s.quantity > 0}
                        - named
                        - portfolio.leftovers.keys()
                    ):
                        log.warning(
                            "leftover_without_reason",
                            user_id=user.user_id,
                            account_id=account.account_id,
                            stock_codes=stranded,
                        )
                    for order in rebalance(
                        portfolio,
                        account,
                        quotes,
                        settings.band,
                        settings.buy_buffer,
                        runs_left,
                        week_runs,
                    ):
                        fields = {
                            "user_id": user.user_id,
                            "account_id": account.account_id,
                            "stock_code": order.stock_code,
                            "side": order.side,
                            "quantity": order.quantity,
                            "reason": order.explanation.reason,
                            **order.pricing.model_dump(),
                        }
                        try:
                            backend.place(user.user_id, account.account_id, order)
                        except httpx.HTTPError as error:
                            counts["failed"] += 1
                            log.error("order_failed", **fields, **_failure(error))
                            continue
                        counts["sent"] += 1
                        counts[order.pricing.order_type] += 1
                        if order.pricing.trigger in ("upper", "lower"):
                            counts[f"{order.pricing.trigger}_triggered"] += 1
                        log.info("order_sent", **fields)

        end.update(portfolio_id=portfolio.id, **{key: counts[key] for key in TOTALS})
        raise SystemExit(1 if counts["failed"] or counts["cancel_failed"] else 0)


if __name__ == "__main__":
    main()
