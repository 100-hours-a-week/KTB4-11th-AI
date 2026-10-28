import pytest
import sqlalchemy as sa
from disclosure_retrieval.app import create_app
from disclosure_retrieval.settings import Settings
from fastapi.testclient import TestClient


@pytest.fixture
def engine(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE corporations RESTART IDENTITY CASCADE"))
    yield pg_engine
    with pg_engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE corporations RESTART IDENTITY CASCADE"))


@pytest.fixture
def client(engine):
    with TestClient(create_app(engine, Settings())) as client:
        yield client


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_RETRIEVAL_POSTGRES_DSN", "unused")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://model/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "test-model")
