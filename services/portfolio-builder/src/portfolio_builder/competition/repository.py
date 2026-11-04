import hashlib
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portfolio_builder.competition.message import Request
from portfolio_builder.database import (
    competition_jobs,
    competition_participants,
    competition_portfolios,
    portfolios,
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


def store_settings(engine: sa.Engine, request: Request) -> tuple[ParticipantSettings, list[str]]:
    with engine.begin() as conn:
        conn.execute(
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
        field.name
        for field in fields(ParticipantSettings)
        if getattr(stored, field.name) != getattr(from_request, field.name)
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
        participant.additional_request,
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

        row = (
            conn.execute(
                sa.select(competition_jobs)
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

        is_stale = datetime.now(timezone.utc) - row["started_at"] > timedelta(
            seconds=stale_after_seconds
        )
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
