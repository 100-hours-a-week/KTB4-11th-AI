import uuid
from contextlib import ExitStack

import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging, start_logging

from portfolio_rebalancer.backend import access_token, authenticate, build_client
from portfolio_rebalancer.market import connect
from portfolio_rebalancer.settings import Settings
from portfolio_rebalancer.tick import tick


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-rebalancer")
    # run_id ties every line of one pass together, so a tick can be read end to end in
    # CloudWatch and two passes can never be confused for one.
    log = get_logger(__name__, run_id=str(uuid.uuid4()))
    with (
        start_logging(log, poll_interval_hint="compose owns the interval") as end,
        ExitStack() as cleanup,
    ):
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
        db = cleanup.enter_context(connect(settings.questdb_conf))
        client = cleanup.enter_context(build_client(settings.backend_url))
        # The access token rides on a cookie and the CSRF token on a header, both set on
        # the client once, because that is where the Backend looks for them.
        authenticate(
            client,
            access_token(
                settings.backend_jwt_secret.get_secret_value(),
                settings.backend_jwt_subject,
                settings.backend_jwt_issuer,
            ),
        )
        # The tick owns its transactions: an order has to be committed before it is sent, so
        # one transaction cannot span the send.
        end["orders_sent"] = tick(engine, db, client, log=log)


if __name__ == "__main__":
    main()
