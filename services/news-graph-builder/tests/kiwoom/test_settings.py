import pytest
from news_graph_builder.kiwoom import KiwoomSettings
from pydantic import ValidationError

REQUIRED = {
    "NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY": "app-key",
    "NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY": "secret-key",
}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = KiwoomSettings()

    assert settings.kiwoom_base_uri == "https://api.kiwoom.com"
    assert settings.kiwoom_request_interval == 0.2


def test_keys_are_hidden_from_repr(required_env):
    settings = KiwoomSettings()

    for key in REQUIRED.values():
        assert key not in repr(settings)


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        KiwoomSettings()


def test_a_negative_interval_raises(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_KIWOOM_REQUEST_INTERVAL", "-1")

    with pytest.raises(ValidationError):
        KiwoomSettings()


def test_secrets_not_in_validation_error(required_env, monkeypatch):
    monkeypatch.delenv("NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY")
    monkeypatch.setenv("NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY", "FAKE-APP-KEY-123")

    with pytest.raises(ValidationError) as info:
        KiwoomSettings()

    assert "FAKE-APP-KEY-123" not in str(info.value)
