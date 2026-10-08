import time
from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from ktb_core.backend_auth import BackendAuth
from ktb_core.logging import get_logger
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, StrictInt

from report_builder.report import build_report, load_evidence, structured_generator
from report_builder.settings import Settings

PROCESSING_TIMEOUT = 260
OPENROUTER_TIMEOUT = 240
BACKEND_TIMEOUT = 10
logger = get_logger(__name__)


class Request(BaseModel):
    competition_id: Annotated[StrictInt, Field(gt=0)]
    participant_id: Annotated[StrictInt, Field(gt=0)]
    user_id: Annotated[StrictInt, Field(gt=0)]
    portfolio_reason_ids: list[UUID] = Field(min_length=1)


def process_message(
    message: dict[str, object],
    sqs: Any,
    auth: BackendAuth,
    engine: sa.Engine,
    structured: Runnable,
) -> None:
    try:
        body = message["Body"]
        if not isinstance(body, str):
            raise ValueError("message body must be a string")
        request = Request.model_validate_json(body)
        evidence = load_evidence(engine, request.portfolio_reason_ids)
        report = build_report(evidence, structured)
        auth.request(
            "POST",
            f"/api/v1/competitions/{request.competition_id}/report/{request.participant_id}",
            str(request.user_id),
            json=report.model_dump(mode="json"),
        )
    except Exception as error:
        logger.error("report_request_failed", error_type=type(error).__name__)
        return
    sqs.delete_message(
        QueueUrl=str(message["_queue_url"]), ReceiptHandle=str(message["ReceiptHandle"])
    )


def main() -> None:
    import boto3
    import httpx
    from botocore.config import Config
    from ktb_core.logging import setup_logging

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
