import pytest
import sqlalchemy as sa

TABLES = "theme_companies, themes, corporation_indices, corporation_aliases, corporations"


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} CASCADE"))


@pytest.fixture
def engine(pg_engine):
    # The code under test commits, so empty the tables instead of rolling back.
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)


def rows(engine, sql: str, **params) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(sa.text(sql), params)]


@pytest.fixture
def query():
    return rows
