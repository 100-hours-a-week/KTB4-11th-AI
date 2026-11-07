from typing import Annotated
from uuid import UUID

import sqlalchemy as sa
from ktb_aws.sqs import Message, Queue
from ktb_core.backend_auth import BackendAuth
from ktb_core.logging import get_logger
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, StrictInt

from report_builder.report import build_report, load_evidence

logger = get_logger(__name__)


class Request(BaseModel):
    competition_id: Annotated[StrictInt, Field(gt=0)]
    participant_id: Annotated[StrictInt, Field(gt=0)]
    user_id: Annotated[StrictInt, Field(gt=0)]
    portfolio_reason_ids: list[UUID]


def process_message(
    message: Message,
    queue: Queue,
    auth: BackendAuth,
    engine: sa.Engine,
    structured: Runnable,
) -> None:
    try:
        request = Request.model_validate_json(message.body)
        evidence = load_evidence(engine, request.portfolio_reason_ids, request.participant_id)
        report = build_report(evidence, structured)
        auth.request(
            "POST",
            f"/api/v1/competitions/{request.competition_id}/report",
            str(request.user_id),
            json=report.model_dump(mode="json"),
        )
    except Exception as error:
        logger.error("report_request_failed", error_type=type(error).__name__)
        return
    queue.delete(message.receipt)
