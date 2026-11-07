import functools
import time

import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging
from langchain_openrouter import ChatOpenRouter

from portfolio_builder.participants.generation import generate_missing_portfolios
from portfolio_builder.settings import Settings

logger = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-builder-worker")
    engine = sa.create_engine(settings.postgres_dsn)
    model_factory = functools.partial(
        ChatOpenRouter, model=settings.llm_model, api_key=settings.llm_api_key
    )
    try:
        while True:
            try:
                generate_missing_portfolios(engine, model_factory, settings, logger)
            except Exception as error:
                logger.exception("generation_cycle_failed", error=str(error))
            time.sleep(settings.worker_poll_interval)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
