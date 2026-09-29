import logging
from contextlib import ExitStack

import sqlalchemy as sa
from ktb_core.logging import setup_logging

from portfolio_rebalancer.backend import bearer_token, build_client
from portfolio_rebalancer.market import connect
from portfolio_rebalancer.settings import Settings
from portfolio_rebalancer.tick import tick

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-rebalancer")
    logger.info("portfolio-rebalancer started")
    with ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
        db = cleanup.enter_context(connect(settings.questdb_conf))
        client = cleanup.enter_context(build_client(settings.backend_url))
        token = bearer_token(
            settings.backend_jwt_secret.get_secret_value(), settings.backend_jwt_subject
        )
        # The tick owns its transactions: an order has to be committed before it is sent, so
        # one transaction cannot span the send.
        sent = tick(engine, db, client, token)
    logger.info("tick finished, %d orders sent", sent)


if __name__ == "__main__":
    main()
