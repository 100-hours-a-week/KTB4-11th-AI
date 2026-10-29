from news_preprocessor.disclosures.settings import Settings


def test_collection_settings_use_news_credentials_and_ignore_embedding_settings(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "sqlite://")
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_BUSINESS_YEAR", "")
    settings = Settings()
    assert settings.business_year is None
    assert settings.dart_api_key.get_secret_value() == "test"
    assert settings.postgres_dsn == "sqlite://"
    assert not hasattr(settings, "embedding_model")
