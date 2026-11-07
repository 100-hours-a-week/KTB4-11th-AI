import pytest
from portfolio_builder.competition.settings import Settings
from pydantic import SecretStr, ValidationError

BASE = {
    "postgres_dsn": "postgresql+psycopg://ktb:test@localhost/ktb_test",
    "queue_url": "https://sqs.example/settings.fifo",
    "jev_api_key": "secret",
    "jev_choice_min_confidence": 0.6,
}


def test_intake_constructs_without_generation_configuration():
    settings = Settings(**BASE)

    assert settings.visibility_timeout == 300
    assert isinstance(settings.jev_api_key, SecretStr)


@pytest.mark.parametrize("name", ["queue_url", "jev_choice_min_confidence"])
def test_intake_requires_its_own_configuration(name, monkeypatch):
    monkeypatch.delenv(f"PORTFOLIO_BUILDER_COMPETITION_{name.upper()}", raising=False)
    values = {key: value for key, value in BASE.items() if key != name}

    with pytest.raises(ValidationError):
        Settings(**values)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("queue_url", ""),
        ("queue_url", "settings.fifo"),
        ("jev_api_key", ""),
        ("visibility_timeout", 0),
        ("visibility_timeout", 43201),
        ("jev_timeout", 0),
        ("jev_noul_threshold", -0.01),
        ("jev_noul_threshold", 1.01),
        ("jev_choice_min_confidence", -0.01),
        ("jev_choice_min_confidence", 1.01),
    ],
)
def test_intake_rejects_invalid_settings(name, value):
    with pytest.raises(ValidationError):
        Settings(**{**BASE, name: value})


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_confidence_floor_accepts_probability_boundaries(value):
    assert (
        Settings(**{**BASE, "jev_choice_min_confidence": value}).jev_choice_min_confidence == value
    )


def test_intake_uses_its_own_environment_prefix(monkeypatch):
    for name, value in BASE.items():
        monkeypatch.setenv(f"PORTFOLIO_BUILDER_COMPETITION_{name.upper()}", str(value))
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_VISIBILITY_TIMEOUT", "600")

    assert Settings().visibility_timeout == 600
