import functools
import os
import time

import boto3
import sqlalchemy as sa
from botocore.config import Config
from ktb_aws.sqs import SqsQueue
from ktb_core.logging import get_logger, setup_logging
from langchain_openrouter import ChatOpenRouter
from pydantic import ValidationError

from portfolio_builder.backend import process_message
from portfolio_builder.builder import generate_missing_portfolios
from portfolio_builder.settings import CompetitionSettings as IntakeSettings
from portfolio_builder.settings import Settings

logger = get_logger(__name__)


def main() -> None:
    setup_logging(
        os.environ.get("PORTFOLIO_BUILDER_LOG_LEVEL", "INFO"),
        service_name="competition-portfolio-builder",
    )
    try:
        settings = Settings()
        intake = IntakeSettings()
    except ValidationError as error:
        logger.error(
            "competition_configuration_invalid",
            fields=[".".join(map(str, item["loc"])) for item in error.errors()],
        )
        raise SystemExit(1) from None
    queue = SqsQueue(
        boto3.client(
            "sqs",
            region_name=intake.aws_region,
            config=Config(connect_timeout=10, read_timeout=25, retries={"max_attempts": 1}),
        ),
        intake.queue_url,
    )
    engine = sa.create_engine(settings.postgres_dsn)
    model_factory = functools.partial(
        ChatOpenRouter, model=settings.llm_model, api_key=settings.llm_api_key
    )
    try:
        while True:
            try:
                for message in queue.receive(visibility=intake.visibility_timeout):
                    process_message(message, queue, engine, intake, logger)
                generate_missing_portfolios(engine, model_factory, settings, logger)
            except Exception as error:
                logger.exception("competition_cycle_failed", error_type=type(error).__name__)
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
