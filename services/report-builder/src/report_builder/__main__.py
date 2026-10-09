from report_builder.worker import process_message
import boto3
import httpx
from botocore.config import Config
from ktb_core.logging import setup_logging

logger = get_logger(__name__)

if __name__ == "__main__":
    settings = Settings()
    setup_logging(settings.log_level, service_name="report-builder")
    logger.info("worker_started")
    sqs = boto3.client(
        "sqs",
        region_name=settings.aws_region,
        config=Config(connect_timeout=10, read_timeout=25, retries={"max_attempts": 1}),
    )
    queue_url = settings.sqs_queue_url
    if not queue_url.startswith("http"):
        queue_url = sqs.get_queue_url(QueueName=queue_url)["QueueUrl"]
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
                    response = sqs.receive_message(
                        QueueUrl=queue_url,
                        MaxNumberOfMessages=1,
                        WaitTimeSeconds=20,
                        VisibilityTimeout=300,
                    )
                    for message in response.get("Messages", []):
                        process_message(
                            {**message, "_queue_url": queue_url}, sqs, auth, engine, structured
                        )
                except Exception as error:
                    logger.error("sqs_poll_failed", error_type=type(error).__name__)
                    time.sleep(1)
        finally:
            engine.dispose()
