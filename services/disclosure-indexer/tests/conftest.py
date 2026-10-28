import pytest


@pytest.fixture(autouse=True)
def embedding_env(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://localhost/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "test-model")
    monkeypatch.setenv("DISCLOSURE_INDEXER_DART_API_KEY", "test")
