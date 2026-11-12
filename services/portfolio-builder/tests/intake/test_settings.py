import pytest
from portfolio_builder.settings import CompetitionSettings as Settings
from pydantic import SecretStr, ValidationError

BASE = {
    "postgres_dsn": "postgresql+psycopg://ktb:test@localhost/ktb_test",
    "queue_url": "https://sqs.example/settings.fifo",
    "jev_api_key": "secret",
    "jev_base_uri": "https://api.typesafe.ai/v1",
    "jev_model": "jev-1.14.0",
    "jev_timeout": 20,
    "jev_noul_threshold": 0.5,
    "jev_choice_min_confidence": 0.6,
}


def test_intake_constructs_without_generation_configuration():
    settings = Settings(**BASE)

    assert settings.visibility_timeout == 300
    assert isinstance(settings.jev_api_key, SecretStr)


@pytest.mark.parametrize("name", [name for name in BASE if name != "postgres_dsn"])
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
        ("jev_api_key", "   "),
        ("jev_api_key", None),
        ("jev_base_uri", ""),
        ("jev_model", ""),
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


def test_jev_and_region_environment_overrides_are_applied(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_AWS_REGION", "us-east-1")
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_BASE_URI", "https://jev.example/v1")
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_MODEL", "custom-jev")
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_JEV_NOUL_THRESHOLD", "0.7")

    settings = Settings(
        **{
            k: v
            for k, v in BASE.items()
            if k not in {"jev_base_uri", "jev_model", "jev_noul_threshold"}
        }
    )

    assert settings.aws_region == "us-east-1"
    assert settings.jev_base_uri == "https://jev.example/v1"
    assert settings.jev_model == "custom-jev"
    assert settings.jev_noul_threshold == 0.7


def test_intake_does_not_use_the_builder_llm_key(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_BUILDER_COMPETITION_JEV_API_KEY", raising=False)
    monkeypatch.setenv("PORTFOLIO_BUILDER_LLM_API_KEY", "builder-key")

    with pytest.raises(ValidationError, match="jev_api_key"):
        Settings(**{k: v for k, v in BASE.items() if k != "jev_api_key"})


@pytest.mark.parametrize("character", [chr(i) for i in range(33)] + [chr(127), "가"])
def test_jev_key_rejects_characters_unsuitable_for_authorization(character):
    key = f"synthetic{character}credential"

    with pytest.raises(ValidationError) as failure:
        Settings(**{**BASE, "jev_api_key": key})

    assert "synthetic" not in str(failure.value)
