import uuid

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging, start_logging

from portfolio_rebalancer.backend import Backend
from portfolio_rebalancer.market import last_closes
from portfolio_rebalancer.portfolio import Unready, load_portfolio
from portfolio_rebalancer.rebalance import rebalance
from portfolio_rebalancer.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-rebalancer")
    log = get_logger(__name__, run_id=str(uuid.uuid4()))
    with start_logging(log, band=settings.band, buy_buffer=settings.buy_buffer) as end:
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

        sent = failed = 0
        with httpx.Client(base_url=settings.backend_url, timeout=10.0) as client:
            backend = Backend(
                client,
                settings.backend_jwt_secret.get_secret_value(),
                settings.backend_jwt_issuer,
            )
            users = backend.users()
            codes = {t.stock_code for t in portfolio.targets} | {
                s.stock_code for u in users for a in u.accounts for s in a.stocks
            }
            closes = last_closes(settings.questdb_conf, codes)
            if missing := sorted(codes - closes.keys()):
                log.warning("no_close", stock_codes=missing)
            named = {t.stock_code for t in portfolio.targets}
            for user in users:
                for account in user.accounts:
                    pending = {o.stock_code for o in account.pending_orders}
                    if stranded := sorted(
                        {s.stock_code for s in account.stocks if s.quantity > 0}
                        - named
                        - pending
                        - portfolio.leftovers.keys()
                    ):
                        log.warning(
                            "leftover_without_reason",
                            user_id=user.user_id,
                            account_id=account.account_id,
                            stock_codes=stranded,
                        )
                    for order in rebalance(
                        portfolio, account, closes, settings.band, settings.buy_buffer
                    ):
                        fields = {
                            "user_id": user.user_id,
                            "account_id": account.account_id,
                            "stock_code": order.stock_code,
                            "side": order.side,
                            "quantity": order.quantity,
                        }
                        try:
                            backend.place(user.user_id, account.account_id, order)
                        except httpx.HTTPStatusError as error:
                            failed += 1
                            log.error(
                                "order_failed",
                                **fields,
                                status=error.response.status_code,
                                body=error.response.text,
                            )
                            continue
                        except httpx.HTTPError as error:
                            failed += 1
                            log.error("order_failed", **fields, error=str(error))
                            continue
                        sent += 1
                        log.info("order_sent", **fields, reason=order.explanation.reason)

        end.update(portfolio_id=portfolio.id, sent=sent, failed=failed)
        raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
