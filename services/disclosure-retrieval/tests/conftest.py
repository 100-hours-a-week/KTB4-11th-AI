import pytest
import sqlalchemy as sa


@pytest.fixture
def engine(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE corporations RESTART IDENTITY CASCADE"))
    yield pg_engine
    with pg_engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE corporations RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://model/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "test-model")
