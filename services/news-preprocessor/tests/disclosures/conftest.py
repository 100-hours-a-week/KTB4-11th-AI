import pytest


@pytest.fixture(autouse=True)
def dart_key(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_DART_API_KEY", "test")
