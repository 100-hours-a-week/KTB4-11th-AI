import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime

import boto3
import httpx
import sqlalchemy as sa
from ktb_aws.sqs import SqsQueue
from ktb_core.logging import (
    StructuredLogger,
    get_logger,
    set_logger_level,
    setup_logging,
    start_logging,
)
from sqlalchemy.exc import MultipleResultsFound, NoResultFound

from portfolio_rebalancer.backend import Backend, order_request_body
from portfolio_rebalancer.holidays import COVERED_THROUGH, KST, hours_left, in_session
from portfolio_rebalancer.market import daily_closes, latest_prices
from portfolio_rebalancer.orders import Orders, PublishError
from portfolio_rebalancer.portfolio import load_portfolio, select_portfolios
from portfolio_rebalancer.rebalance import quote, rebalance
from portfolio_rebalancer.settings import Settings
from portfolio_rebalancer.snapshot import Account

TOTALS = (
    "cancel_requested",
    "cancel_failed",
    "explanation_failed",
    "portfolio_failed",
    "identity_failed",
    "sent",
    "failed",
    "limit",
    "market",
    "upper_triggered",
    "lower_triggered",
)


def _cancel_all(
    orders: Orders,
    log: StructuredLogger,
    user_id: int,
    account: Account,
    counts: Counter[str],
    portfolio_id: int,
) -> bool:
    for pending in account.pending_orders:
        fields = {
            "user_id": user_id,
            "portfolio_id": portfolio_id,
            "participant_id": account.participant_id,
            "account_id": account.account_id,
            "stock_code": pending.stock_code,
            "side": pending.order_side,
            "order_id": pending.order_id,
        }
        try:
            published = orders.cancel(user_id, account.account_id, pending.order_id)
        except PublishError as error:
            counts["cancel_failed"] += 1
            log.error(
                "cancel_failed",
                **fields,
                **error.fields,
                result="failed",
                error=str(error),
                queue_role="orders",
                direction="publish",
            )
            return False
        counts["cancel_requested"] += 1
        log.info(
            "order_cancel_requested",
            **fields,
            **published,
            result="published",
            queue_role="orders",
            direction="publish",
            order_type=pending.order_type,
            limit_price=pending.limit_price,
            quantity=pending.quantity,
        )
    return True


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-rebalancer")
    set_logger_level("botocore", "WARNING")
    run_id = str(uuid.uuid4())
    log = get_logger(__name__, run_id=run_id)
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

        counts: Counter[str] = Counter()
        engine = sa.create_engine(settings.postgres_dsn)
        try:
            portfolio = load_portfolio(engine)
        except (
            KeyError,
            ValueError,
            TypeError,
            NoResultFound,
            MultipleResultsFound,
        ) as error:
            portfolio = None
            counts["portfolio_failed"] += 1
            log.error("common_portfolio_failed", error_type=type(error).__name__)
        finally:
            engine.dispose()

        sqs = boto3.client("sqs")
        orders = Orders(SqsQueue(sqs, settings.order_queue_url), run_id)
        with httpx.Client(base_url=settings.backend_url, timeout=10.0) as client:
            backend = Backend(
                client, settings.backend_jwt_secret.get_secret_value(), settings.backend_jwt_issuer
            )
            failed_cancels: set[int] = set()
            cancel_requested_by_account: dict[int, set[int]] = defaultdict(set)
            try:
                users = backend.users()
                log.info("users_received", user_count=len(users))
            except (httpx.HTTPError, ValueError) as error:
                reason = (
                    "users_fetch_failed"
                    if isinstance(error, httpx.HTTPError)
                    else "users_payload_invalid"
                )
                detail = type(error).__name__
                if isinstance(error, httpx.HTTPStatusError):
                    detail += f": HTTP {error.response.status_code}"
                log.error("users_fetch_failed", reason=reason, error=detail, result="failed")
                end.update(
                    outcome="users_fetch_failed",
                    error=detail,
                    portfolio_id=portfolio.id if portfolio is not None else None,
                    **{key: counts[key] for key in TOTALS},
                )
                raise SystemExit(1) from error
            engine = sa.create_engine(settings.postgres_dsn)
            try:
                selected = select_portfolios(engine, users, portfolio, log, counts)
            finally:
                engine.dispose()
            for user_id, account, chosen in selected:
                if _cancel_all(orders, log, user_id, account, counts, chosen.id):
                    cancel_requested_by_account[account.account_id].update(
                        pending.order_id for pending in account.pending_orders
                    )
                else:
                    failed_cancels.add(account.account_id)
            codes = {t.stock_code for _, _, p in selected for t in p.targets} | {
                s.stock_code for _, a, _ in selected for s in a.stocks
            }
            closes = daily_closes(settings.questdb_conf, codes, now)
            prices = latest_prices(settings.questdb_conf, codes)
            if missing := sorted(codes - closes.keys()):
                log.warning("no_price", stock_codes=missing, missing="daily_closes")
            if missing := sorted(codes - prices.keys()):
                log.warning("no_price", stock_codes=missing, missing="latest_price")
            quotes = {c: quote(closes[c], prices[c]) for c in closes.keys() & prices.keys()}
            for user_id, account, chosen in selected:
                named = {t.stock_code for t in chosen.targets}
                if account.account_id in failed_cancels:
                    continue
                replaced = sorted(cancel_requested_by_account[account.account_id])
                if stranded := sorted(
                    {s.stock_code for s in account.stocks if s.quantity > 0}
                    - named
                    - chosen.leftovers.keys()
                ):
                    log.warning(
                        "leftover_without_reason",
                        user_id=user_id,
                        portfolio_id=chosen.id,
                        participant_id=account.participant_id,
                        account_id=account.account_id,
                        stock_codes=stranded,
                    )
                for order in rebalance(
                    chosen,
                    account,
                    quotes,
                    settings.band,
                    settings.buy_buffer,
                    runs_left,
                    week_runs,
                ):
                    request_body = order_request_body(order)
                    fields = {
                        "user_id": user_id,
                        "portfolio_id": chosen.id,
                        "participant_id": account.participant_id,
                        "account_id": account.account_id,
                        "stock_code": order.stock_code,
                        "stock_name": order.stock_name,
                        "request_body": request_body,
                        "side": order.side,
                        "quantity": order.quantity,
                        "reason": order.explanation.reason,
                        **order.pricing.model_dump(),
                    }
                    try:
                        published = orders.place(
                            user_id, account.account_id, request_body, replaced
                        )
                    except PublishError as error:
                        counts["failed"] += 1
                        log.error(
                            "order_failed",
                            **fields,
                            **error.fields,
                            result="failed",
                            error=str(error),
                            queue_role="orders",
                            direction="publish",
                        )
                        continue
                    counts["sent"] += 1
                    counts[order.pricing.order_type] += 1
                    if order.pricing.trigger in ("upper", "lower"):
                        counts[f"{order.pricing.trigger}_triggered"] += 1
                    log.info(
                        "order_sent",
                        **fields,
                        **published,
                        result="published",
                        queue_role="orders",
                        direction="publish",
                    )
            end.update(
                portfolio_id=portfolio.id if portfolio is not None else None,
                **{key: counts[key] for key in TOTALS},
            )
            if counts["failed"] or counts["cancel_failed"]:
                raise SystemExit(1)
            raise SystemExit(
                2
                if counts["explanation_failed"]
                or counts["portfolio_failed"]
                or counts["identity_failed"]
                else 0
            )


if __name__ == "__main__":
    main()
