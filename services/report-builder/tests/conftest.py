import pytest
import sqlalchemy as sa

TABLES = (
    "competition_portfolios, competition_participants, portfolio_reasons, portfolios, corporations"
)


def _truncate(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    _truncate(pg_engine)
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations (stock_code, corp_code, name) VALUES"
                " ('005930', '00126380', '삼성전자')"
            )
        )
    yield pg_engine
    _truncate(pg_engine)
