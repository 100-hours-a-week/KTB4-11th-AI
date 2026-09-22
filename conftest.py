"""Database fixtures shared by every package's tests."""

import os

import pytest
import sqlalchemy as sa

TEST_DSN_ENV = "KTB_TEST_POSTGRES_DSN"


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = os.environ.get(TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"{TEST_DSN_ENV} is not set")
    return dsn


@pytest.fixture(scope="session")
def pg_engine(pg_dsn):
    engine = sa.create_engine(pg_dsn)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_conn(pg_engine):
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()
