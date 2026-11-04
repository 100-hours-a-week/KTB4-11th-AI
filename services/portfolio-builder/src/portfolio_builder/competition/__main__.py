import functools
import time

import boto3
import httpx
import sqlalchemy as sa
from botocore.config import Config
from ktb_core.backend_auth import BackendAuth
from ktb_core.logging import get_logger, setup_logging
from langchain_openrouter import ChatOpenRouter

from portfolio_builder.competition.backend import Backend
from portfolio_builder.competition.worker import process_message
from portfolio_builder.settings import Settings

logger = get_logger(__name__)

REQUIRED_SETTINGS = (
    "competition_queue_url",
    "backend_base_uri",
    "backend_jwt_issuer",
)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="competition-portfolio-builder")
    missing = [name for name in REQUIRED_SETTINGS if not getattr(settings, name)]
    if not settings.backend_jwt_secret.get_secret_value():
        missing.append("backend_jwt_secret")
    if missing:
        logger.error("missing_required_settings", missing=missing)
        raise SystemExit(f"missing required settings: {', '.join(missing)}")

    logger.info("worker_started")
    sqs = boto3.client(
        "sqs",
        region_name=settings.aws_region,
        config=Config(connect_timeout=10, read_timeout=25, retries={"max_attempts": 1}),
    )
    queue_url = settings.competition_queue_url
    if not queue_url.startswith("http"):
        queue_url = sqs.get_queue_url(QueueName=queue_url)["QueueUrl"]
    engine = sa.create_engine(settings.postgres_dsn)
    model_factory = functools.partial(
        ChatOpenRouter, model=settings.llm_model, api_key=settings.llm_api_key
    )
    with httpx.Client(
        base_url=settings.backend_base_uri, timeout=settings.backend_timeout
    ) as client:
        auth = BackendAuth(
            client, settings.backend_jwt_secret.get_secret_value(), settings.backend_jwt_issuer
        )
        backend = Backend(auth)
        try:
            while True:
                try:
                    response = sqs.receive_message(
                        QueueUrl=queue_url,
                        MaxNumberOfMessages=1,
                        WaitTimeSeconds=20,
                        VisibilityTimeout=settings.competition_visibility_timeout,
                    )
                    for message in response.get("Messages", []):
                        process_message(
                            {**message, "_queue_url": queue_url},
                            sqs,
                            engine,
                            backend,
                            model_factory,
                            settings,
                            logger,
                        )
                except Exception as error:
                    logger.error("sqs_poll_failed", error_type=type(error).__name__)
                    time.sleep(1)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
