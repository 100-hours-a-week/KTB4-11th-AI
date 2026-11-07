import time

import boto3
import sqlalchemy as sa
from botocore.config import Config
from ktb_aws.sqs import SqsQueue
from ktb_core.logging import get_logger, setup_logging

from competition.intake import process_message
from competition.settings import Settings

logger = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="competition")
    logger.info("worker_started")
    queue = SqsQueue(
        boto3.client(
            "sqs",
            region_name=settings.aws_region,
            config=Config(connect_timeout=10, read_timeout=25, retries={"max_attempts": 1}),
        ),
        settings.queue_url,
    )
    engine = sa.create_engine(settings.postgres_dsn)
    try:
        while True:
            try:
                for message in queue.receive(visibility=settings.visibility_timeout):
                    process_message(message, queue, engine, settings, logger)
            except Exception as error:
                logger.error("sqs_poll_failed", error_type=type(error).__name__, error=str(error))
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
