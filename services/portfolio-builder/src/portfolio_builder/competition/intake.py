from functools import partial
from typing import Any

import sqlalchemy as sa
from ktb_core.logging import StructuredLogger
from pydantic import ValidationError

from portfolio_builder.competition import jev
from portfolio_builder.competition.canonical import classify_additional_request
from portfolio_builder.competition.message import Request
from portfolio_builder.competition.repository import store_settings
from portfolio_builder.competition.themes import ResolvedAgent, UnresolvedTheme, resolve_agent
from portfolio_builder.settings import Settings

JEV_CHOICE_MIN_CONFIDENCE_FALLBACK = 0.5


def _classify(
    engine: sa.Engine,
    settings: Settings,
    log: StructuredLogger,
    resolved: ResolvedAgent,
) -> tuple[Any, str | None, bool]:
    ask = partial(
        jev.ask,
        settings.jev_base_uri,
        settings.jev_model,
        settings.llm_api_key,
        timeout=settings.jev_timeout,
    )
    choice_min_confidence = settings.jev_choice_min_confidence
    if choice_min_confidence is None:
        choice_min_confidence = JEV_CHOICE_MIN_CONFIDENCE_FALLBACK
    return classify_additional_request(
        engine,
        ask,
        settings.jev_model,
        settings.jev_noul_threshold,
        choice_min_confidence,
        resolved.additional_request,
        resolved.interested_theme_names,
        log,
    )


def process_message(
    message: dict[str, object],
    sqs: Any,
    engine: sa.Engine,
    settings: Settings,
    log: StructuredLogger,
) -> None:
    queue_url = str(message["_queue_url"])
    receipt_handle = str(message["ReceiptHandle"])

    try:
        request = Request.model_validate_json(str(message["Body"]))
    except ValidationError as error:
        log.error("invalid_message", error_type=type(error).__name__)
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        return

    try:
        resolved_agents = [
            resolve_agent(engine, request.competition_id, request.user_id, agent)
            for agent in request.agents
        ]
    except UnresolvedTheme as error:
        log.error("unresolved_theme", error_type=type(error).__name__, error=str(error))
        return

    for resolved in resolved_agents:
        participant, differing_fields = store_settings(
            engine, resolved, partial(_classify, engine, settings, log)
        )
        if differing_fields:
            log.warning(
                "settings_mismatch",
                participant_id=participant.participant_id,
                fields=differing_fields,
            )

    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
