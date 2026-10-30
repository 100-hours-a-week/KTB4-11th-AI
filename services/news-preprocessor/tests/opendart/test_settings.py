from news_preprocessor.sources.publishers.opendart.settings import Settings


def test_parsing_settings_do_not_require_database_or_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_ARCHIVE_ROOT", str(tmp_path))
    settings = Settings()
    assert settings.archive_root == tmp_path
    assert not hasattr(settings, "dart_api_key")
    assert not hasattr(settings, "postgres_dsn")
    assert not hasattr(settings, "embedding_model")
