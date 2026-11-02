from datetime import date

import pytest
from news_preprocessor.sources.publishers.opendart.settings import Settings
from pydantic import ValidationError


def test_parsing_settings_do_not_require_database_or_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_ARCHIVE_ROOT", str(tmp_path))
    settings = Settings()
    assert settings.archive_root == tmp_path
    assert not hasattr(settings, "dart_api_key")
    assert not hasattr(settings, "postgres_dsn")
    assert not hasattr(settings, "embedding_model")


def test_historical_dates_load_from_environment(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_START_DATE", "2025-01-01")
    monkeypatch.setenv("NEWS_PREPROCESSOR_DISCLOSURE_END_DATE", "2025-12-31")
    settings = Settings()
    assert settings.start_date == date(2025, 1, 1)
    assert settings.end_date == date(2025, 12, 31)


@pytest.mark.parametrize(
    "dates",
    [
        {"start_date": "2025-01-01"},
        {"end_date": "2025-12-31"},
        {"start_date": "2025-12-31", "end_date": "2025-01-01"},
    ],
)
def test_historical_dates_require_a_complete_ordered_range(dates):
    with pytest.raises(ValidationError):
        Settings(**dates)
