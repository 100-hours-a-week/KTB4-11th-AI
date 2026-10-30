from news_preprocessor.sources.publishers.opendart.disclosures.settings import Settings


def test_collection_settings_use_news_credentials_and_ignore_embedding_settings(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "sqlite://")
    settings = Settings()
    assert not hasattr(settings, "business_year")
    assert settings.dart_api_key.get_secret_value() == "test"
    assert not hasattr(settings, "postgres_dsn")
    assert not hasattr(settings, "embedding_model")
