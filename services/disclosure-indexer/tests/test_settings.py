import pytest
from disclosure_indexer.settings import Settings
from pydantic import ValidationError


def test_disclosure_settings_ignore_news_embedding_configuration(monkeypatch):
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", "http://news/v1")
    monkeypatch.setenv("KTB_EMBEDDING_MODEL", "news-model")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://disclosures/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "finetuned-qwen3-8b")
    settings = Settings(postgres_dsn="unused", dart_api_key="test")
    assert settings.embedding_base_uri == "http://disclosures/v1"
    assert settings.embedding_model == "finetuned-qwen3-8b"
    assert settings.dimensions == 2000


def test_news_model_is_not_a_fallback(monkeypatch):
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("KTB_EMBEDDING_MODEL", "news-model")
    with pytest.raises(ValidationError):
        Settings(
            postgres_dsn="unused",
            dart_api_key="test",
            DISCLOSURE_EMBEDDING_BASE_URI="http://disclosures/v1",
        )


def test_blank_optional_business_year_from_compose_means_default(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_INDEXER_BUSINESS_YEAR", "")
    assert Settings(postgres_dsn="unused").business_year is None
