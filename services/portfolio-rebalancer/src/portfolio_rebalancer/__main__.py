"""One tick of the rebalancer, run on a schedule.

The service is invoked once per poll and exits, the way `market-collector` is; compose owns
the interval. Nothing calls this service, so it has no HTTP server: every input is a
datastore read or an outbound call, and the order ladder only narrows because a later tick
finds the pair still outstanding.
"""

import logging

import sqlalchemy as sa
from ktb_core.logging import setup_logging

from portfolio_rebalancer.external.backend import bearer_token, build_client
from portfolio_rebalancer.external.prices import connect
from portfolio_rebalancer.settings import Settings
from portfolio_rebalancer.tick import tick

SERVICE_NAME = "portfolio-rebalancer"


def main() -> None:
    settings = Settings()
    # service_name is required from #54 onwards; see packages/core/src/ktb_core/logging.py.
    setup_logging(settings.log_level, service_name=SERVICE_NAME)
    log = logging.getLogger(__name__)

    engine = sa.create_engine(settings.postgres_dsn)
    try:
        with (
            connect(settings.questdb_conf) as db,
            build_client(settings.backend_url) as client,
        ):
            token = bearer_token(settings.backend_jwt.get_secret_value())
            # The tick owns its transactions: an order has to be committed before it is
            # sent, so one transaction cannot span the send.
            sent = tick(engine, db, client, token)
        log.info("tick finished", extra={"orders_sent": sent})
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
