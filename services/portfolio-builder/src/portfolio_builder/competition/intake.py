from functools import partial
from typing import Any

import sqlalchemy as sa
from ktb_aws.sqs import Message, Queue
from ktb_core.logging import StructuredLogger
from pydantic import ValidationError

from portfolio_builder.competition import jev
from portfolio_builder.competition.canonical import classify_additional_request
from portfolio_builder.competition.message import Request
from portfolio_builder.competition.repository import store_settings
from portfolio_builder.competition.settings import Settings
from portfolio_builder.competition.themes import ResolvedAgent, UnknownTheme, resolve_agent


def _classify(
    engine: sa.Engine,
    settings: Settings,
    log: StructuredLogger,
    resolved: ResolvedAgent,
) -> tuple[Any, str | None, bool]:
    if settings.jev_api_key is None:
        raise ValueError("Jev credential is required during classification")
    ask = partial(
        jev.ask,
        settings.jev_base_uri,
        settings.jev_model,
        settings.jev_api_key,
        timeout=settings.jev_timeout,
    )
    return classify_additional_request(
        engine,
        ask,
        settings.jev_model,
        settings.jev_noul_threshold,
        settings.jev_choice_min_confidence,
        resolved.additional_request,
        resolved.interested_theme_names,
        log,
    )


def process_message(
    message: Message,
    queue: Queue,
    engine: sa.Engine,
    settings: Settings,
    log: StructuredLogger,
) -> None:
    try:
        request = Request.model_validate_json(message.body)
    except ValidationError as error:
        log.error("invalid_message", error_type=type(error).__name__, error=str(error))
        return

    try:
        resolved_agents = [
            resolve_agent(engine, request.competition_id, request.user_id, agent)
            for agent in request.agents
        ]
    except UnknownTheme as error:
        log.error(
            "unknown_theme",
            request_id=request.request_id,
            competition_id=request.competition_id,
            error_type=type(error).__name__,
            error=str(error),
        )
        return

    for resolved in resolved_agents:
        participant, differing_fields = store_settings(
            engine, resolved, partial(_classify, engine, settings, log)
        )
        if differing_fields:
            log.warning(
                "settings_mismatch",
                request_id=request.request_id,
                participant_id=participant.participant_id,
                fields=differing_fields,
            )

    log.info(
        "intake",
        request_id=request.request_id,
        competition_id=request.competition_id,
        participant_ids=[agent.participant_id for agent in request.agents],
    )
    queue.delete(message.receipt)
