import pytest
from market_analyzer_mcp.settings import Settings
from pydantic import ValidationError

REQUIRED = {"MARKET_ANALYZER_MCP_QUESTDB_DSN": "postgresql://admin:quest@localhost:8812/qdb"}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.candle_limit == 200


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [("MARKET_ANALYZER_MCP_PORT", "0"), ("MARKET_ANALYZER_MCP_CANDLE_LIMIT", "0")],
)
def test_out_of_range_values_raise(required_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()
