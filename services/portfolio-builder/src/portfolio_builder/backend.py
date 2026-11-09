from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from typing import Annotated, Any

import httpx
import sqlalchemy as sa
from ktb_aws.sqs import Message, Queue
from ktb_core.backend_auth import BackendAuth
from ktb_core.logging import StructuredLogger
from pydantic import BaseModel, BeforeValidator, Field, StrictInt, ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portfolio_builder.competition import canonical
from portfolio_builder.competition.canonical import classify_additional_request
from portfolio_builder.competition.database import competition_participants
from portfolio_builder.competition.repository import ParticipantSettings, participant_settings
from portfolio_builder.settings import CompetitionSettings as Settings

MAX_ADDITIONAL_REQUEST_CHARS = 4000


MAX_REQUEST_ID_CHARS = 128


MAX_AGENTS = 16


def _null_as_empty(value: object) -> object:
    return "" if value is None else value


AdditionalRequest = Annotated[
    str,
    BeforeValidator(_null_as_empty),
    Field(max_length=MAX_ADDITIONAL_REQUEST_CHARS),
]


RequestId = Annotated[
    str,
    BeforeValidator(_null_as_empty),
    Field(max_length=MAX_REQUEST_ID_CHARS),
]


RISK_LEVEL_TO_PROFILE: dict[int, str] = {
    1: "안정형",
    2: "안정추구형",
    3: "위험중립형",
    4: "성장추구형",
    5: "성장형",
}


class AgentSettings(BaseModel):
    participant_id: Annotated[StrictInt, Field(gt=0)]
    risk_level: Annotated[StrictInt, Field(ge=1, le=5)]
    preferred_themes: list[str] = Field(default_factory=list)
    excluded_themes: list[str] = Field(default_factory=list)
    additional_request: AdditionalRequest = ""

    @property
    def risk_profile(self) -> str:
        return RISK_LEVEL_TO_PROFILE[self.risk_level]


class Request(BaseModel):
    request_id: RequestId = ""
    competition_id: Annotated[StrictInt, Field(gt=0)]
    user_id: Annotated[StrictInt, Field(gt=0)]
    agents: Annotated[list[AgentSettings], Field(min_length=1, max_length=MAX_AGENTS)]


class UnknownTheme(Exception):
    pass


def theme_names(engine: sa.Engine, codes: Sequence[str]) -> dict[str, str]:
    unique_codes = list(dict.fromkeys(codes))
    if not unique_codes:
        return {}

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT theme_code, name FROM themes WHERE theme_code IN :codes").bindparams(
                sa.bindparam("codes", expanding=True)
            ),
            {"codes": unique_codes},
        )
        name_by_code = {code: name for code, name in rows}

    missing = [code for code in unique_codes if code not in name_by_code]
    if missing:
        raise UnknownTheme(f"unknown theme code(s): {', '.join(missing)}")
    return name_by_code


def resolve_agent(
    engine: sa.Engine, competition_id: int, user_id: int, agent: AgentSettings
) -> ParticipantSettings:
    name_by_code = theme_names(engine, [*agent.preferred_themes, *agent.excluded_themes])

    excluded_codes = sorted(set(agent.excluded_themes))
    interested_codes = sorted(set(agent.preferred_themes) - set(excluded_codes))

    return ParticipantSettings(
        competition_id=competition_id,
        user_id=user_id,
        participant_id=agent.participant_id,
        risk_profile=agent.risk_profile,
        interested_theme_codes=tuple(interested_codes),
        excluded_theme_codes=tuple(excluded_codes),
        interested_theme_names=tuple(name_by_code[code] for code in interested_codes),
        excluded_theme_names=tuple(name_by_code[code] for code in excluded_codes),
        additional_request=agent.additional_request,
    )


def _classify(
    engine: sa.Engine,
    settings: Settings,
    log: StructuredLogger,
    resolved: ParticipantSettings,
) -> tuple[Any, str | None, bool]:
    if settings.jev_api_key is None:
        raise ValueError("Jev credential is required during classification")
    ask = partial(
        canonical.ask,
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


SETTINGS_DRIFT_FIELDS = (
    "participant_id",
    "competition_id",
    "user_id",
    "risk_profile",
    "interested_theme_codes",
    "excluded_theme_codes",
    "interested_theme_names",
    "excluded_theme_names",
    "additional_request",
)


ClassifyFn = Callable[[ParticipantSettings], tuple[Any, str | None, bool]]


def _unclassified(resolved: ParticipantSettings) -> tuple[Any, str | None, bool]:
    return resolved.additional_request, None, False


def store_settings(
    engine: sa.Engine,
    resolved: ParticipantSettings,
    classify: ClassifyFn = _unclassified,
) -> tuple[ParticipantSettings, list[str]]:
    excluded_codes = sorted(set(resolved.excluded_theme_codes))
    interested_codes = sorted(set(resolved.interested_theme_codes) - set(excluded_codes))

    with engine.begin() as conn:
        inserted = conn.execute(
            pg_insert(competition_participants)
            .values(
                participant_id=resolved.participant_id,
                competition_id=resolved.competition_id,
                user_id=resolved.user_id,
                risk_profile=resolved.risk_profile,
                interested_theme_codes=interested_codes,
                excluded_theme_codes=excluded_codes,
                interested_theme_names=resolved.interested_theme_names,
                excluded_theme_names=resolved.excluded_theme_names,
                additional_request=resolved.additional_request,
            )
            .on_conflict_do_nothing(index_elements=["participant_id"])
            .returning(competition_participants.c.participant_id)
        ).first()

        if inserted is not None:
            canonical_form, canonical_version, classified = classify(resolved)
            conn.execute(
                sa.update(competition_participants)
                .where(competition_participants.c.participant_id == resolved.participant_id)
                .values(
                    canonical_form=canonical_form,
                    canonical_version=canonical_version,
                    classified=classified,
                )
            )

        row = (
            conn.execute(
                sa.select(competition_participants).where(
                    competition_participants.c.participant_id == resolved.participant_id
                )
            )
            .mappings()
            .one()
        )

    stored = participant_settings(row)
    from_request = replace(
        resolved,
        interested_theme_codes=tuple(interested_codes),
        excluded_theme_codes=tuple(excluded_codes),
    )
    differing = [
        name
        for name in SETTINGS_DRIFT_FIELDS
        if getattr(stored, name) != getattr(from_request, name)
    ]
    return stored, differing


class Account(BaseModel):
    account_id: int = Field(gt=0, strict=True)
    participant_id: int | None = Field(gt=0, strict=True)
    is_active: bool


class User(BaseModel):
    user_id: int = Field(gt=0, strict=True)
    accounts: list[Account]


class Snapshot(BaseModel):
    users: list[User]


def active_participants(client: httpx.Client, secret: str, issuer: str) -> dict[int, int]:
    response = BackendAuth(client, secret, issuer).get("/api/v1/users/ai-server", "ai-server")
    snapshot = Snapshot.model_validate(response.json())
    active: dict[int, int] = {}
    accounts: set[int] = set()
    participants: set[int] = set()
    for user in snapshot.users:
        for account in user.accounts:
            if account.account_id in accounts:
                raise ValueError("duplicate account identity")
            accounts.add(account.account_id)
            if account.participant_id is None:
                continue
            if account.participant_id in participants:
                raise ValueError("duplicate participant identity")
            participants.add(account.participant_id)
            if account.is_active:
                active[account.participant_id] = user.user_id
    return active
