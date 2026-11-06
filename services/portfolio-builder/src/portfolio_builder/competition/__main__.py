import functools
import time

import boto3
import sqlalchemy as sa
from botocore.config import Config
from ktb_aws.sqs import SqsQueue
from ktb_core.logging import get_logger, setup_logging
from langchain_openrouter import ChatOpenRouter

from portfolio_builder.competition.generation import generate_missing_portfolios
from portfolio_builder.competition.intake import process_message
from portfolio_builder.settings import Settings

logger = get_logger(__name__)

REQUIRED_SETTINGS = ("competition_queue_url",)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="competition-portfolio-builder")
    missing = [name for name in REQUIRED_SETTINGS if not getattr(settings, name)]
    if settings.jev_choice_min_confidence is None:
        missing.append("jev_choice_min_confidence")
    if missing:
        logger.error("missing_required_settings", missing=missing)
        raise SystemExit(f"missing required settings: {', '.join(missing)}")

    logger.info("worker_started")
    queue = SqsQueue(
        boto3.client(
            "sqs",
            region_name=settings.aws_region,
            config=Config(connect_timeout=10, read_timeout=25, retries={"max_attempts": 1}),
        ),
        settings.competition_queue_url,
    )
    engine = sa.create_engine(settings.postgres_dsn)
    model_factory = functools.partial(
        ChatOpenRouter, model=settings.llm_model, api_key=settings.llm_api_key
    )
    try:
        while True:
            try:
                for message in queue.receive(visibility=settings.competition_visibility_timeout):
                    process_message(message, queue, engine, settings, logger)
                generate_missing_portfolios(engine, model_factory, settings, logger)
            except Exception as error:
                logger.error("sqs_poll_failed", error_type=type(error).__name__, error=str(error))
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
