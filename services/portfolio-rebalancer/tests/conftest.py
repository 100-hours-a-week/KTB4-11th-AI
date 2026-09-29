"""Fixtures for the store tests. Everything here skips unless a test database is given."""

import pathlib

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REPO_ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / "alembic.ini").is_file()
)


@pytest.fixture(scope="session")
def migrated(pg_dsn, pg_engine):
    """Bring the schema to head once."""
    import os

    os.environ["KTB_POSTGRES_DSN"] = pg_dsn
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    return pg_engine


@pytest.fixture
def conn(migrated):
    """One transaction per test, rolled back, so tests cannot see each other's rows."""
    with migrated.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def portfolio_id(conn):
    """rebalance_orders references portfolios.id, so a row has to exist to point at."""
    return conn.execute(
        sa.text(
            "INSERT INTO portfolios (cash_weight, commentary, model)"
            " VALUES (0.1, '', 'test') RETURNING id"
        )
    ).scalar()
