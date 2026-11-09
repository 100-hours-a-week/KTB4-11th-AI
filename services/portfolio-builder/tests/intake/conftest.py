import pytest
import sqlalchemy as sa
from portfolio_builder.competition.repository import ParticipantSettings


@pytest.fixture
def engine(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE competition_participants, themes CASCADE"))
        conn.execute(
            sa.text(
                "INSERT INTO themes (theme_code, name) VALUES ('T1', 'HBM'), ('T2', 'AI반도체')"
            )
        )
    yield pg_engine
    with pg_engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE competition_participants, themes CASCADE"))


@pytest.fixture
def _request():
    def make(**changes):
        return ParticipantSettings(
            **{
                "competition_id": 10,
                "user_id": 100,
                "participant_id": 1,
                "risk_profile": "성장형",
                "interested_theme_codes": ("T1",),
                "excluded_theme_codes": (),
                "interested_theme_names": ("HBM",),
                "excluded_theme_names": (),
                "additional_request": "원문",
                **changes,
            }
        )

    return make
