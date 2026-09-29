"""One tick of the rebalancer, run on a schedule.

The service is invoked once per poll and exits, the way `market-collector` is; compose
owns the interval. Nothing calls this service, so it has no HTTP server: every input is a
datastore read or an outbound call, and the order ladder only narrows because a later tick
finds the pair still outstanding.
"""

import logging

from ktb_core.logging import setup_logging

from portfolio_rebalancer.backend import acquire_token, build_client
from portfolio_rebalancer.prices import connect
from portfolio_rebalancer.settings import Settings

SERVICE_NAME = "portfolio-rebalancer"


def main() -> None:
    settings = Settings()
    # service_name is required from #54 onwards; see packages/core/src/ktb_core/logging.py.
    setup_logging(settings.log_level, service_name=SERVICE_NAME)
    logging.getLogger(__name__).info("portfolio-rebalancer tick starting")

    with connect(settings.questdb_conf) as db, build_client(settings.backend_url) as client:
        token = acquire_token(client)
        tick(db, client, token)


def tick(db: object, client: object, token: str) -> None:
    """Poll, decide, send.

    The model portfolio and the order history live in PostgreSQL, which #54 has not landed
    yet, so there is nothing to read a portfolio from. Refusing here beats polling the
    Backend and silently deciding nothing.
    """
    raise NotImplementedError(
        "the PostgreSQL store lands with #54; see docs/superpowers/plans/"
        "2026-09-28-portfolio-rebalancer.md task 5"
    )


if __name__ == "__main__":
    main()
