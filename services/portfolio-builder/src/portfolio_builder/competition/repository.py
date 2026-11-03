from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portfolio_builder.competition.message import Request
from portfolio_builder.database import competition_participants


@dataclass(frozen=True)
class ParticipantSettings:
    participant_id: int
    competition_id: int
    user_id: int
    risk_profile: str
    interested_theme_codes: tuple[str, ...]
    excluded_theme_codes: tuple[str, ...]
    additional_request: str


def store_settings(engine: sa.Engine, request: Request) -> tuple[ParticipantSettings, bool]:
    with engine.begin() as conn:
        conn.execute(
            pg_insert(competition_participants)
            .values(
                participant_id=request.participant_id,
                competition_id=request.competition_id,
                user_id=request.user_id,
                risk_profile=request.risk_profile,
                interested_theme_codes=request.interested_theme_codes,
                excluded_theme_codes=request.excluded_theme_codes,
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
        interested_theme_codes=tuple(request.interested_theme_codes),
        excluded_theme_codes=tuple(request.excluded_theme_codes),
        additional_request=request.additional_request,
    )
    return stored, stored != from_request
