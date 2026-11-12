import hashlib
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from ktb_core.krx import KST
from ktb_core.logging import StructuredLogger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portfolio_builder.competition.canonical import canonical_form_part
from portfolio_builder.competition.database import (
    competition_attempts,
    competition_jobs,
    competition_participants,
    competition_portfolios,
)
from portfolio_builder.database import portfolios


@dataclass(frozen=True)
class ParticipantSettings:
    participant_id: int
    competition_id: int
    user_id: int
    risk_profile: str
    interested_theme_codes: tuple[str, ...]
    excluded_theme_codes: tuple[str, ...]
    interested_theme_names: tuple[str, ...]
    excluded_theme_names: tuple[str, ...]
    additional_request: str
    canonical_form: Any = None
    canonical_version: str | None = None
    classified: bool = False


def participant_settings(row: sa.RowMapping) -> ParticipantSettings:
    return ParticipantSettings(
        participant_id=row["participant_id"],
        competition_id=row["competition_id"],
        user_id=row["user_id"],
        risk_profile=row["risk_profile"],
        interested_theme_codes=tuple(row["interested_theme_codes"]),
        excluded_theme_codes=tuple(row["excluded_theme_codes"]),
        interested_theme_names=tuple(row["interested_theme_names"]),
        excluded_theme_names=tuple(row["excluded_theme_names"]),
        additional_request=row["additional_request"],
        canonical_form=row["canonical_form"],
        canonical_version=row["canonical_version"],
        classified=row["classified"],
    )


@dataclass(frozen=True)
class Claim:
    mine: bool
    portfolio_id: int | None
    generation_key: str
    attempt: int
    participant_id: int


class ClaimLost(RuntimeError):
    pass


def generation_key(
    participant: ParticipantSettings,
    previous_portfolio_id: int | None,
    llm_model: str,
) -> str:
    parts = [
        "v2",
        participant.risk_profile,
        str(len(participant.interested_theme_codes)),
        *participant.interested_theme_codes,
        str(len(participant.excluded_theme_codes)),
        *participant.excluded_theme_codes,
        participant.additional_request,
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

        def owe_outcome() -> None:
            conn.execute(
                pg_insert(competition_attempts)
                .values(participant_id=participant.participant_id, generation_key=key)
                .on_conflict_do_nothing(index_elements=["participant_id", "generation_key"])
            )

        def spend_attempt() -> None:
            owe_outcome()
            conn.execute(
                sa.update(competition_attempts)
                .where(
                    competition_attempts.c.participant_id == participant.participant_id,
                    competition_attempts.c.generation_key == key,
                )
                .values(attempts=competition_attempts.c.attempts + 1)
            )

        if inserted is not None:
            spend_attempt()
            return Claim(True, None, key, 1, participant.participant_id)

        stale_before = sa.func.now() - timedelta(seconds=stale_after_seconds)
        row = (
            conn.execute(
                sa.select(
                    competition_jobs,
                    (competition_jobs.c.started_at < stale_before).label("is_stale"),
                    sa.func.coalesce(competition_attempts.c.attempts, 0).label(
                        "participant_attempts"
                    ),
                )
                .select_from(
                    competition_jobs.outerjoin(
                        competition_attempts,
                        sa.and_(
                            competition_attempts.c.generation_key
                            == competition_jobs.c.generation_key,
                            competition_attempts.c.participant_id == participant.participant_id,
                        ),
                    )
                )
                .where(competition_jobs.c.generation_key == key)
                .with_for_update(of=competition_jobs)
            )
            .mappings()
            .one()
        )
        if row["status"] == "succeeded":
            if row["portfolio_id"] is None:
                return None
            owe_outcome()
            return Claim(
                False, row["portfolio_id"], key, row["attempts"], participant.participant_id
            )
        reclaimable = row["status"] == "failed" or (row["status"] == "running" and row["is_stale"])
        if not reclaimable or row["participant_attempts"] >= max_attempts:
            return None
        conn.execute(
            sa.update(competition_jobs)
            .where(competition_jobs.c.generation_key == key)
            .values(
                status="running",
                attempts=competition_jobs.c.attempts + 1,
                participant_id=participant.participant_id,
                started_at=sa.func.now(),
                finished_at=None,
                error=None,
                portfolio_id=None,
            )
        )
        spend_attempt()
        return Claim(True, None, key, row["attempts"] + 1, participant.participant_id)


def finish_job(conn: sa.Connection, claim: Claim, portfolio_id: int) -> bool:
    if not claim.mine:
        return False
    return (
        conn.execute(
            sa.update(competition_jobs)
            .where(
                competition_jobs.c.generation_key == claim.generation_key,
                competition_jobs.c.attempts == claim.attempt,
                competition_jobs.c.participant_id == claim.participant_id,
                sa.or_(
                    competition_jobs.c.status == "running",
                    sa.and_(
                        competition_jobs.c.status == "succeeded",
                        competition_jobs.c.portfolio_id == portfolio_id,
                    ),
                ),
                sa.or_(
                    competition_jobs.c.portfolio_id.is_(None),
                    competition_jobs.c.portfolio_id == portfolio_id,
                ),
            )
            .values(
                status="succeeded",
                portfolio_id=portfolio_id,
                error=None,
                finished_at=sa.func.coalesce(competition_jobs.c.finished_at, sa.func.now()),
            )
            .returning(competition_jobs.c.generation_key)
        ).first()
        is not None
    )


def fail_job(engine: sa.Engine, claim: Claim, error: str) -> bool:
    if not claim.mine:
        return False
    with engine.begin() as conn:
        return (
            conn.execute(
                sa.update(competition_jobs)
                .where(
                    competition_jobs.c.generation_key == claim.generation_key,
                    competition_jobs.c.attempts == claim.attempt,
                    competition_jobs.c.participant_id == claim.participant_id,
                    competition_jobs.c.status == "running",
                    competition_jobs.c.portfolio_id.is_(None),
                )
                .values(status="failed", error=error, finished_at=sa.func.now())
                .returning(competition_jobs.c.generation_key)
            ).first()
            is not None
        )


def link_portfolio(bind: sa.Engine | sa.Connection, participant_id: int, portfolio_id: int) -> None:
    with bind.begin() if isinstance(bind, sa.Engine) else nullcontext(bind) as conn:
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


def participants_without_ready_portfolio(
    engine: sa.Engine, max_explanation_attempts: int
) -> list[ParticipantSettings]:
    latest = (
        sa.select(
            competition_portfolios.c.participant_id,
            sa.func.max(competition_portfolios.c.portfolio_id).label("portfolio_id"),
        )
        .group_by(competition_portfolios.c.participant_id)
        .subquery()
    )
    with engine.connect() as conn:
        rows = (
            conn.execute(
                sa.select(competition_participants)
                .select_from(
                    competition_participants.outerjoin(
                        latest,
                        latest.c.participant_id == competition_participants.c.participant_id,
                    ).outerjoin(portfolios, portfolios.c.id == latest.c.portfolio_id)
                )
                .where(
                    sa.or_(
                        portfolios.c.status.is_(None),
                        sa.and_(
                            portfolios.c.status != "ready",
                            portfolios.c.explanation_attempts < max_explanation_attempts,
                        ),
                    )
                )
                .order_by(competition_participants.c.participant_id)
            )
            .mappings()
            .all()
        )
    return [participant_settings(row) for row in rows]


def participants_due_for_generation(
    engine: sa.Engine,
    active: dict[int, int],
    now: datetime,
    max_explanation_attempts: int,
) -> list[tuple[ParticipantSettings, int | None]]:
    if not active:
        return []
    start = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    latest = (
        sa.select(
            competition_portfolios.c.participant_id,
            sa.func.max(competition_portfolios.c.portfolio_id).label("portfolio_id"),
        )
        .group_by(competition_portfolios.c.participant_id)
        .subquery()
    )
    with engine.connect() as conn:
        rows = (
            conn.execute(
                sa.select(competition_participants, latest.c.portfolio_id.label("previous_id"))
                .select_from(
                    competition_participants.outerjoin(
                        latest, latest.c.participant_id == competition_participants.c.participant_id
                    )
                    .outerjoin(
                        competition_portfolios,
                        sa.and_(
                            competition_portfolios.c.participant_id
                            == competition_participants.c.participant_id,
                            competition_portfolios.c.portfolio_id == latest.c.portfolio_id,
                        ),
                    )
                    .outerjoin(portfolios, portfolios.c.id == latest.c.portfolio_id)
                )
                .where(
                    competition_participants.c.participant_id.in_(active),
                    sa.or_(
                        latest.c.portfolio_id.is_(None),
                        sa.and_(
                            portfolios.c.status != "ready",
                            portfolios.c.explanation_attempts < max_explanation_attempts,
                        ),
                        sa.and_(
                            portfolios.c.status == "ready",
                            competition_portfolios.c.created_at < start,
                        ),
                    ),
                )
                .order_by(competition_participants.c.participant_id)
            )
            .mappings()
            .all()
        )
    return [
        (participant_settings(row), row["previous_id"])
        for row in rows
        if active[row["participant_id"]] == row["user_id"]
    ]


def excluded_company_ids(engine: sa.Engine, excluded_theme_codes: Sequence[str]) -> frozenset[str]:
    if not excluded_theme_codes:
        return frozenset()
    query = sa.text(
        "SELECT corporations.corp_code FROM theme_corporations"
        " JOIN corporations ON corporations.stock_code = theme_corporations.stock_code"
        " WHERE theme_corporations.theme_code IN :theme_codes"
    ).bindparams(sa.bindparam("theme_codes", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(query, {"theme_codes": list(excluded_theme_codes)})
        return frozenset(row[0] for row in rows)


def record_shared_explanation_failures(
    conn: sa.Connection,
    max_attempts: int,
    log: StructuredLogger,
    portfolio_id: int | None = None,
) -> None:
    query = (
        sa.select(
            competition_portfolios.c.participant_id,
            competition_portfolios.c.portfolio_id,
            competition_jobs.c.generation_key,
        )
        .select_from(
            competition_portfolios.join(
                portfolios, portfolios.c.id == competition_portfolios.c.portfolio_id
            ).join(competition_jobs, competition_jobs.c.portfolio_id == portfolios.c.id)
        )
        .where(
            portfolios.c.status != "ready",
            portfolios.c.explanation_attempts >= max_attempts,
            competition_jobs.c.status == "succeeded",
            competition_jobs.c.participant_id != competition_portfolios.c.participant_id,
            competition_portfolios.c.explanation_failed_at.is_(None),
        )
        .order_by(competition_portfolios.c.participant_id, competition_portfolios.c.portfolio_id)
        .with_for_update(of=competition_portfolios, skip_locked=True)
    )
    if portfolio_id is not None:
        query = query.where(competition_portfolios.c.portfolio_id == portfolio_id)
    for row in conn.execute(query).all():
        marked = conn.execute(
            sa.update(competition_portfolios)
            .where(
                competition_portfolios.c.participant_id == row.participant_id,
                competition_portfolios.c.portfolio_id == row.portfolio_id,
                competition_portfolios.c.explanation_failed_at.is_(None),
            )
            .values(explanation_failed_at=sa.func.now())
            .returning(competition_portfolios.c.participant_id)
        ).first()
        if marked is not None:
            log.error(
                "shared_explanation_failed",
                participant_id=row.participant_id,
                portfolio_id=row.portfolio_id,
                key=row.generation_key,
                reason="explanation_budget_exhausted",
            )
