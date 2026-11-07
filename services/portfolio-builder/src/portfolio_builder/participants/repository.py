import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portfolio_builder.briefing.dto import PreviousPortfolio
from portfolio_builder.briefing.repository import load_previous_portfolio
from portfolio_builder.database import portfolios
from portfolio_builder.participants.database import (
    competition_attempts,
    competition_jobs,
    competition_participants,
    competition_portfolios,
)


@dataclass(frozen=True)
class ParticipantSettings:
    participant_id: int
    competition_id: int
    user_id: int
    agent_id: int
    risk_profile: str
    interested_theme_codes: tuple[str, ...]
    excluded_theme_codes: tuple[str, ...]
    interested_theme_names: tuple[str, ...]
    excluded_theme_names: tuple[str, ...]
    additional_request: str
    canonical_form: Any = None
    canonical_version: str | None = None
    classified: bool = False


def _participant(row: sa.RowMapping) -> ParticipantSettings:
    return ParticipantSettings(
        participant_id=row["participant_id"],
        competition_id=row["competition_id"],
        user_id=row["user_id"],
        agent_id=row["agent_id"],
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


def generation_key(
    participant: ParticipantSettings,
    previous_portfolio_id: int | None,
    llm_model: str,
) -> str:
    canonical = participant.canonical_form
    if canonical is None:
        canonical = ""
    elif not isinstance(canonical, str):
        canonical = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    parts = [
        "v1",
        participant.risk_profile,
        str(len(participant.interested_theme_codes)),
        *participant.interested_theme_codes,
        str(len(participant.excluded_theme_codes)),
        *participant.excluded_theme_codes,
        canonical,
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
            return Claim(mine=True, portfolio_id=None)

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
            return Claim(mine=False, portfolio_id=row["portfolio_id"])
        reclaimable = row["status"] == "failed" or (row["status"] == "running" and row["is_stale"])
        if not reclaimable or row["participant_attempts"] >= max_attempts:
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
        spend_attempt()
        return Claim(mine=True, portfolio_id=None)


def finish_job(engine: sa.Engine, key: str, portfolio_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.update(competition_jobs)
            .where(
                competition_jobs.c.generation_key == key,
                competition_jobs.c.status != "succeeded",
            )
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
    return [_participant(row) for row in rows]


def find_participant_portfolio(
    conn: sa.Connection, participant_id: int
) -> PreviousPortfolio | None:
    portfolio = (
        conn.execute(
            sa.text(
                "SELECT p.id, p.created_at, p.cash_weight, p.commentary FROM portfolios p"
                " JOIN competition_portfolios cp ON cp.portfolio_id = p.id"
                " WHERE cp.participant_id = :participant_id"
                " ORDER BY p.id DESC LIMIT 1"
            ),
            {"participant_id": participant_id},
        )
        .mappings()
        .first()
    )
    return load_previous_portfolio(conn, portfolio)
