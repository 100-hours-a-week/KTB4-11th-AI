import time

import boto3
import httpx
import sqlalchemy as sa
from ktb_aws.sqs import POLL_CONFIG, SqsQueue
from ktb_core.backend_auth import BackendAuth
from ktb_core.logging import get_logger, setup_logging

from report_builder.report import structured_generator
from report_builder.settings import Settings
from report_builder.worker import process_message

logger = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="report-builder")
    logger.info("worker_started")
    queue = SqsQueue(
        boto3.client("sqs", region_name=settings.aws_region, config=POLL_CONFIG),
        settings.sqs_queue_url,
    )
    engine = sa.create_engine(settings.postgres_dsn)
    with httpx.Client(
        base_url=settings.backend_base_uri, timeout=settings.backend_timeout
    ) as client:
        auth = BackendAuth(
            client,
            settings.backend_jwt_secret.get_secret_value(),
            settings.backend_jwt_issuer,
        )
        structured = structured_generator(
            settings.llm_model,
            settings.llm_api_key.get_secret_value(),
            settings.llm_timeout,
        )
        try:
            while True:
                try:
                    for message in queue.receive(limit=1, wait=20, visibility=300):
                        process_message(message, queue, auth, engine, structured)
                except Exception as error:
                    logger.error("sqs_poll_failed", error_type=type(error).__name__)
                    time.sleep(1)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
