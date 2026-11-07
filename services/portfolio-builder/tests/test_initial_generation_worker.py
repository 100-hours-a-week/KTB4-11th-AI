from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from portfolio_builder.participants import __main__ as entry


def test_worker_generates_from_database_without_queue_settings(monkeypatch):
    settings = SimpleNamespace(
        postgres_dsn="postgresql+psycopg://unused",
        log_level="INFO",
        llm_model="model",
        llm_api_key="key",
        worker_poll_interval=2,
    )
    engine = Mock()
    generate = Mock(side_effect=[None, KeyboardInterrupt])
    sleep = Mock()
    monkeypatch.setattr(entry, "Settings", lambda: settings)
    monkeypatch.setattr(entry.sa, "create_engine", lambda _: engine)
    monkeypatch.setattr(entry, "generate_missing_portfolios", generate)
    monkeypatch.setattr(entry.time, "sleep", sleep)

    with pytest.raises(KeyboardInterrupt):
        entry.main()

    assert generate.call_count == 2
    assert generate.call_args.args[0] is engine
    sleep.assert_called_once_with(2)
    engine.dispose.assert_called_once()
