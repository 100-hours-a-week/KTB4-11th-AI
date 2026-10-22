from contextlib import nullcontext
from types import SimpleNamespace

from market_streamer import __main__ as entry


def test_main_enters_tracing_after_setup_logging(monkeypatch):
    calls = []

    async def run(settings, now):
        pass

    monkeypatch.setattr(
        entry, "Settings", lambda: SimpleNamespace(log_level="INFO", index_name="x")
    )
    monkeypatch.setattr(entry, "setup_logging", lambda *a, **k: calls.append("setup_logging"))
    monkeypatch.setattr(entry, "run", run)
    monkeypatch.setattr(entry, "tracing", lambda name: calls.append(name) or nullcontext())

    entry.main()

    assert calls == ["setup_logging", "market-streamer"]
