from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from ktb_core.backend_auth import BackendAuth
from ktb_core.logging import get_logger
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, StrictInt

from report_builder.report import EvidenceError, build_report, load_evidence

logger = get_logger(__name__)


class Request(BaseModel):
    competition_id: Annotated[StrictInt, Field(gt=0)]
    participant_id: Annotated[StrictInt, Field(gt=0)]
    user_id: Annotated[StrictInt, Field(gt=0)]
    portfolio_reason_ids: list[UUID]


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
        evidence = load_evidence(engine, request.portfolio_reason_ids, request.participant_id)
        report = build_report(evidence, structured)
        auth.request(
            "POST",
            f"/api/v1/competitions/{request.competition_id}/report",
            str(request.user_id),
            json={"participant_id": request.participant_id, **report.model_dump(mode="json")},
        )
    except EvidenceError as error:
        logger.error(
            "report_request_failed",
            error_type=type(error).__name__,
            competition_id=request.competition_id,
            participant_id=request.participant_id,
            user_id=request.user_id,
            reason="missing_evidence",
            error=str(error),
        )
        return
    except Exception as error:
        logger.error("report_request_failed", error_type=type(error).__name__)
        return
    sqs.delete_message(
        QueueUrl=str(message["_queue_url"]), ReceiptHandle=str(message["ReceiptHandle"])
    )
