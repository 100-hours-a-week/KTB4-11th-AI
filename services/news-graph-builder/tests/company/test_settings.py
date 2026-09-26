import pytest
from news_graph_builder.company.settings import CompanySettings
from pydantic import ValidationError

REQUIRED = {
    "NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY": "app-key",
    "NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY": "secret-key",
    "NEWS_GRAPH_BUILDER_DART_API_KEY": "dart-key",
}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = CompanySettings()

    assert settings.kiwoom_base_uri == "https://api.kiwoom.com"


def test_keys_are_hidden_from_repr(required_env):
    settings = CompanySettings()

    assert settings.dart_api_key.get_secret_value() == "dart-key"
    for key in REQUIRED.values():
        assert key not in repr(settings)


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        CompanySettings()


def test_secrets_not_in_validation_error(required_env, monkeypatch):
    monkeypatch.delenv("NEWS_GRAPH_BUILDER_DART_API_KEY")
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY", "FAKE-APP-KEY-123")

    with pytest.raises(ValidationError) as info:
        CompanySettings()

    assert "FAKE-APP-KEY-123" not in str(info.value)
    assert "input_value" not in str(info.value)
