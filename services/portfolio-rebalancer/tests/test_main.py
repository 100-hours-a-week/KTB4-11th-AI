import pytest
from portfolio_rebalancer import __main__
from portfolio_rebalancer.__main__ import SERVICE_NAME, tick


def test_a_tick_refuses_while_the_store_is_missing():
    """#54 carries the PostgreSQL store. Until it lands, a tick must fail loudly rather
    than poll the Backend and decide nothing."""
    with pytest.raises(NotImplementedError, match="#54"):
        tick(db=object(), client=object(), token="a-token")


def test_logging_is_set_up_under_the_service_name(monkeypatch):
    """#54 makes service_name a required keyword of setup_logging, so a tick that omitted
    it would die on startup. Stubbed, because core's signature lands with #54."""
    calls = []
    monkeypatch.setattr(
        __main__,
        "setup_logging",
        lambda level, *, service_name: calls.append((level, service_name)),
    )
    monkeypatch.setattr(__main__, "Settings", lambda: _settings())
    monkeypatch.setattr(__main__, "connect", lambda conf: _closing())
    monkeypatch.setattr(__main__, "build_client", lambda url: _closing())
    monkeypatch.setattr(__main__, "acquire_token", lambda client: "a-token")

    with pytest.raises(NotImplementedError):
        __main__.main()

    assert calls == [("INFO", SERVICE_NAME)]


class _settings:
    log_level = "INFO"
    questdb_conf = "ws::addr=questdb:9000;"
    backend_url = "http://backend:8080"
    postgres_dsn = "postgresql+psycopg://ktb:ktb@postgres:5432/ktb"


class _closing:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None
