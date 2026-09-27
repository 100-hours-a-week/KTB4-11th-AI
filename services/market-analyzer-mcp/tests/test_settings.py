import pytest
from market_analyzer_mcp.settings import Settings
from pydantic import ValidationError


def test_the_questdb_dsn_is_required():
    with pytest.raises(ValidationError, match="questdb_dsn"):
        Settings(_env_file=None)


def test_the_defaults_let_another_container_reach_the_server():
    """A loopback bind makes the SDK answer a service-name Host header with 421, so
    the default has to be 0.0.0.0."""
    settings = Settings(questdb_dsn="postgresql://localhost:8812/qdb")

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.candle_limit == 200


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_an_unusable_port_is_rejected(port):
    with pytest.raises(ValidationError):
        Settings(questdb_dsn="postgresql://localhost:8812/qdb", port=port)


def test_a_non_positive_candle_limit_is_rejected():
    with pytest.raises(ValidationError):
        Settings(questdb_dsn="postgresql://localhost:8812/qdb", candle_limit=0)
