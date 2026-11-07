from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from competition.database import competition_participants
from competition.themes import ResolvedAgent

SETTINGS_DRIFT_FIELDS = (
    "participant_id",
    "competition_id",
    "user_id",
    "agent_id",
    "risk_profile",
    "interested_theme_codes",
    "excluded_theme_codes",
    "interested_theme_names",
    "excluded_theme_names",
    "additional_request",
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


ClassifyFn = Callable[[ResolvedAgent], tuple[Any, str | None, bool]]


def _unclassified(resolved: ResolvedAgent) -> tuple[Any, str | None, bool]:
    return resolved.additional_request, None, False


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


def store_settings(
    engine: sa.Engine,
    resolved: ResolvedAgent,
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
                agent_id=resolved.agent_id,
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

    stored = _participant(row)
    from_request = ParticipantSettings(
        participant_id=resolved.participant_id,
        competition_id=resolved.competition_id,
        user_id=resolved.user_id,
        agent_id=resolved.agent_id,
        risk_profile=resolved.risk_profile,
        interested_theme_codes=tuple(interested_codes),
        excluded_theme_codes=tuple(excluded_codes),
        interested_theme_names=tuple(resolved.interested_theme_names),
        excluded_theme_names=tuple(resolved.excluded_theme_names),
        additional_request=resolved.additional_request,
    )
    differing = [
        name
        for name in SETTINGS_DRIFT_FIELDS
        if getattr(stored, name) != getattr(from_request, name)
    ]
    return stored, differing
