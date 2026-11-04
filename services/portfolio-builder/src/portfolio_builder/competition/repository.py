import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portfolio_builder.competition.canonical import canonical_form_part
from portfolio_builder.competition.message import Request
from portfolio_builder.database import (
    competition_jobs,
    competition_participants,
    competition_portfolios,
    portfolios,
)

SETTINGS_DRIFT_FIELDS = (
    "participant_id",
    "competition_id",
    "user_id",
    "risk_profile",
    "interested_theme_codes",
    "excluded_theme_codes",
    "additional_request",
)


@dataclass(frozen=True)
class ParticipantSettings:
    participant_id: int
    competition_id: int
    user_id: int
    risk_profile: str
    interested_theme_codes: tuple[str, ...]
    excluded_theme_codes: tuple[str, ...]
    additional_request: str
    canonical_form: Any = None
    canonical_version: str | None = None
    classified: bool = False


ClassifyFn = Callable[[Request], tuple[Any, str | None, bool]]


def _unclassified(request: Request) -> tuple[Any, str | None, bool]:
    return request.additional_request, None, False


def store_settings(
    engine: sa.Engine,
    request: Request,
    classify: ClassifyFn = _unclassified,
) -> tuple[ParticipantSettings, list[str]]:
    with engine.begin() as conn:
        inserted = conn.execute(
            pg_insert(competition_participants)
            .values(
                participant_id=request.participant_id,
                competition_id=request.competition_id,
                user_id=request.user_id,
                risk_profile=request.risk_profile,
                interested_theme_codes=request.effective_interested,
                excluded_theme_codes=request.effective_excluded,
                additional_request=request.additional_request,
            )
            .on_conflict_do_nothing(index_elements=["participant_id"])
            .returning(competition_participants.c.participant_id)
        ).first()

        if inserted is not None:
            canonical_form, canonical_version, classified = classify(request)
            conn.execute(
                sa.update(competition_participants)
                .where(competition_participants.c.participant_id == request.participant_id)
                .values(
                    canonical_form=canonical_form,
                    canonical_version=canonical_version,
                    classified=classified,
                )
            )

        row = (
            conn.execute(
                sa.select(competition_participants).where(
                    competition_participants.c.participant_id == request.participant_id
                )
            )
            .mappings()
            .one()
        )

    stored = ParticipantSettings(
        participant_id=row["participant_id"],
        competition_id=row["competition_id"],
        user_id=row["user_id"],
        risk_profile=row["risk_profile"],
        interested_theme_codes=tuple(row["interested_theme_codes"]),
        excluded_theme_codes=tuple(row["excluded_theme_codes"]),
        additional_request=row["additional_request"],
        canonical_form=row["canonical_form"],
        canonical_version=row["canonical_version"],
        classified=row["classified"],
    )
    from_request = ParticipantSettings(
        participant_id=request.participant_id,
        competition_id=request.competition_id,
        user_id=request.user_id,
        risk_profile=request.risk_profile,
        interested_theme_codes=tuple(request.effective_interested),
        excluded_theme_codes=tuple(request.effective_excluded),
        additional_request=request.additional_request,
    )
    differing = [
        name
        for name in SETTINGS_DRIFT_FIELDS
        if getattr(stored, name) != getattr(from_request, name)
    ]
    return stored, differing


@dataclass(frozen=True)
class Claim:
    mine: bool
    portfolio_id: int | None


def generation_key(
    participant: ParticipantSettings,
    previous_portfolio_id: int | None,
    llm_model: str,
) -> str:
    parts = [
        "v1",
        participant.risk_profile,
        str(len(participant.interested_theme_codes)),
        *participant.interested_theme_codes,
        str(len(participant.excluded_theme_codes)),
        *participant.excluded_theme_codes,
        canonical_form_part(participant.canonical_form),
        participant.canonical_version or "",
        str(previous_portfolio_id or 0),
        llm_model,
    ]
    encoded = b"".join(f"{len(part.encode())}:".encode() + part.encode() for part in parts)
    return hashlib.sha256(encoded).hexdigest()


def claim_job(
    engine: sa.Engine,
    key: str,
    participant: ParticipantSettings,
    previous_portfolio_id: int | None,
    stale_after_seconds: int,
    max_attempts: int,
) -> Claim | None:
    with engine.begin() as conn:
        inserted = conn.execute(
            pg_insert(competition_jobs)
            .values(
                generation_key=key,
                participant_id=participant.participant_id,
                previous_portfolio_id=previous_portfolio_id,
                status="running",
            )
            .on_conflict_do_nothing(index_elements=["generation_key"])
            .returning(competition_jobs.c.generation_key)
        ).first()
        if inserted is not None:
            return Claim(mine=True, portfolio_id=None)

        stale_before = sa.func.now() - timedelta(seconds=stale_after_seconds)
        row = (
            conn.execute(
                sa.select(
                    competition_jobs,
                    (competition_jobs.c.started_at < stale_before).label("is_stale"),
                )
                .where(competition_jobs.c.generation_key == key)
                .with_for_update()
            )
            .mappings()
            .one()
        )

        if row["status"] == "succeeded":
            if row["portfolio_id"] is None:
                return None
            return Claim(mine=False, portfolio_id=row["portfolio_id"])

        is_stale = row["is_stale"]
        reclaimable = (
            row["status"] == "failed" or (row["status"] == "running" and is_stale)
        ) and row["attempts"] < max_attempts
        if not reclaimable:
            return None

        conn.execute(
            sa.update(competition_jobs)
            .where(competition_jobs.c.generation_key == key)
            .values(
                status="running",
                attempts=competition_jobs.c.attempts + 1,
                started_at=sa.func.now(),
                finished_at=None,
                error=None,
                portfolio_id=None,
            )
        )
        return Claim(mine=True, portfolio_id=None)


def finish_job(engine: sa.Engine, key: str, portfolio_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.update(competition_jobs)
            .where(competition_jobs.c.generation_key == key)
            .values(status="succeeded", portfolio_id=portfolio_id, finished_at=sa.func.now())
        )


def fail_job(engine: sa.Engine, key: str, error: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.update(competition_jobs)
            .where(competition_jobs.c.generation_key == key)
            .values(status="failed", error=error, finished_at=sa.func.now())
        )


def link_portfolio(engine: sa.Engine, participant_id: int, portfolio_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            pg_insert(competition_portfolios)
            .values(participant_id=participant_id, portfolio_id=portfolio_id)
            .on_conflict_do_nothing(index_elements=["participant_id", "portfolio_id"])
        )


def latest_participant_portfolio(
    engine: sa.Engine, participant_id: int
) -> tuple[int, str, bool] | None:
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(
                portfolios.c.id,
                portfolios.c.status,
                portfolios.c.trace.isnot(None).label("has_trace"),
            )
            .select_from(
                competition_portfolios.join(
                    portfolios, portfolios.c.id == competition_portfolios.c.portfolio_id
                )
            )
            .where(competition_portfolios.c.participant_id == participant_id)
            .order_by(portfolios.c.id.desc())
            .limit(1)
        ).first()
    if row is None:
        return None
    return row.id, row.status, row.has_trace
