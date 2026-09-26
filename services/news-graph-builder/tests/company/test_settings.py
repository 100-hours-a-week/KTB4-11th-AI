import pytest
from news_graph_builder.company.settings import CompanySettings
from pydantic import ValidationError


def test_the_dart_key_is_hidden_from_repr(monkeypatch):
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_DART_API_KEY", "dart-key")

    settings = CompanySettings()

    assert settings.dart_api_key.get_secret_value() == "dart-key"
    assert "dart-key" not in repr(settings)


def test_a_missing_dart_key_raises(monkeypatch):
    monkeypatch.delenv("NEWS_GRAPH_BUILDER_DART_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        CompanySettings()
