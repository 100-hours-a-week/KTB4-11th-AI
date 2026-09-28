import json

import pytest
import talib
from portfolio_builder import __main__ as entry
from portfolio_builder.agent import RunResult
from portfolio_builder.briefing import Briefing

REQUIRED = {
    "PORTFOLIO_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_BUILDER_QUESTDB_CONF": "http::addr=localhost:9000;",
    "PORTFOLIO_BUILDER_OPENROUTER_API_KEY": "sk-or-v1-secret",
    "PORTFOLIO_BUILDER_LLM_MODEL": "openai/gpt-5.5",
}
BRIEFING = Briefing(None, frozenset(), 0, 0, [1], 1, 1, "brief")


def test_ta_lib_is_importable():
    assert "ROCP" in talib.get_functions()


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "load_briefing", lambda engine, days: BRIEFING)


def _events(out):
    return [json.loads(line) for line in out.splitlines()]


def test_a_saved_portfolio_exits_zero(env, monkeypatch, capsys):
    captured = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return RunResult("saved", 7, 3, {"input": 10})

    monkeypatch.setattr(entry, "run_agent", fake_run_agent)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    out = capsys.readouterr().out
    events = _events(out)
    assert [e["message"] for e in events] == ["run_start", "ingestion", "prompt", "run_end"]
    assert events[-1]["outcome"] == "saved"
    assert events[-1]["portfolio_id"] == 7
    assert len({e["run_id"] for e in events}) == 1
    assert {t.name for t in captured["tools"]} == {
        "get_news_cluster",
        "search_news_cluster",
        "search_graph",
        "find_graph_paths",
        "analyze_technicals",
        "submit_portfolio",
    }
    assert captured["max_turns"] == 150
    assert "sk-or-v1-secret" not in out


def test_max_turns_exits_one(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("max_turns", None, 150, {}))

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    assert _events(capsys.readouterr().out)[-1]["level"] == "ERROR"


def test_a_failure_before_the_agent_exits_one_with_run_end(env, monkeypatch, capsys):
    def broken(engine, days):
        raise RuntimeError("postgres unreachable")

    monkeypatch.setattr(entry, "load_briefing", broken)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    last = _events(capsys.readouterr().out)[-1]
    assert last["message"] == "run_end"
    assert last["outcome"] == "error"
    assert "postgres unreachable" in last["error"]
