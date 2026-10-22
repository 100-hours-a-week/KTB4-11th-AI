from contextlib import nullcontext

from market_http import __main__ as entry
from market_http.settings import Settings


def test_main_bounds_postgres_connections_and_queries(monkeypatch):
    settings = Settings(
        questdb_conf="ws::addr=localhost:9000;",
        postgres_dsn="postgresql+psycopg://ktb@localhost/ktb",
        postgres_pool_size=3,
        postgres_timeout_seconds=4,
        valkey_host="localhost",
    )
    engine = object()
    app = object()
    calls = {}

    def create_engine(dsn, **kwargs):
        calls["engine"] = (dsn, kwargs)
        return engine

    def create_app(*args, **kwargs):
        calls["app"] = kwargs
        return app

    monkeypatch.setattr(entry, "Settings", lambda: settings)
    monkeypatch.setattr(entry, "setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(entry, "connect_valkey", lambda settings: object())
    monkeypatch.setattr(entry.sa, "create_engine", create_engine)
    monkeypatch.setattr(entry, "create_app", create_app)
    monkeypatch.setattr(entry, "tracing", lambda name: calls.update(tracing=name) or nullcontext())
    monkeypatch.setattr(entry.uvicorn, "run", lambda *args, **kwargs: None)

    entry.main()

    assert calls["engine"] == (
        settings.postgres_dsn,
        {
            "pool_size": 3,
            "max_overflow": 0,
            "pool_timeout": 4,
            "pool_pre_ping": True,
            "connect_args": {"connect_timeout": 4, "options": "-c statement_timeout=4000"},
        },
    )
    assert calls["app"]["postgres_engine"] is engine
    assert calls["tracing"] == "market-http"
