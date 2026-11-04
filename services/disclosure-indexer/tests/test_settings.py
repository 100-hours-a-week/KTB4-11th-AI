import pytest
from disclosure_indexer.settings import Settings
from pydantic import ValidationError


def test_disclosure_settings_ignore_news_embedding_configuration(monkeypatch):
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", "http://news/v1")
    monkeypatch.setenv("KTB_EMBEDDING_MODEL", "news-model")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://disclosures/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "finetuned-qwen3-8b")
    settings = Settings(postgres_dsn="unused")
    assert settings.embedding_base_uri == "http://disclosures/v1"
    assert settings.embedding_model == "finetuned-qwen3-8b"
    assert settings.dimensions == 2000


def test_news_model_is_not_a_fallback(monkeypatch):
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("KTB_EMBEDDING_MODEL", "news-model")
    with pytest.raises(ValidationError):
        Settings(
            postgres_dsn="unused",
            DISCLOSURE_EMBEDDING_BASE_URI="http://disclosures/v1",
        )
