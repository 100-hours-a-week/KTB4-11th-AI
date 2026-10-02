import json

import pytest
import talib
from portfolio_builder import __main__ as entry
from portfolio_builder.agent.run import RunResult
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.briefing import Briefing
from portfolio_builder.explain import ExplanationRejected, Explanations

REQUIRED = {
    "PORTFOLIO_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_BUILDER_QUESTDB_CONF": "ws::addr=localhost:9000;",
    "PORTFOLIO_BUILDER_OPENROUTER_API_KEY": "test-openrouter-key",
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
    calls = {}
    monkeypatch.setattr(entry, "save_trace", lambda engine, pid, trace: calls.update(trace=pid))
    monkeypatch.setattr(entry, "load_unexplained", lambda engine: None)
    monkeypatch.setattr(entry, "load_targets", lambda engine, pid: [])
    monkeypatch.setattr(entry, "explain", lambda *args: Explanations(stocks=[]))
    monkeypatch.setattr(
        entry, "save_explanations", lambda engine, pid, explanations: calls.update(saved=pid)
    )
    monkeypatch.setattr(
        entry, "mark_explanation_failed", lambda engine, pid: calls.update(failed=pid)
    )
    return calls


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
    messages = [e["message"] for e in events]
    assert messages == ["run_start", "ingestion", "prompt", "explained", "run_end"]
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
    assert "test-openrouter-key" not in out
    assert env == {"trace": 7, "saved": 7}


def test_max_turns_exits_one(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("max_turns", None, 150, {}))

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    assert _events(capsys.readouterr().out)[-1]["level"] == "ERROR"


def test_a_failure_before_the_agent_raises_after_run_end(env, monkeypatch, capsys):
    def broken(engine, days):
        raise RuntimeError("postgres unreachable")

    monkeypatch.setattr(entry, "load_briefing", broken)

    with pytest.raises(RuntimeError):
        entry.main()

    last = _events(capsys.readouterr().out)[-1]
    assert last["message"] == "run_end"
    assert last["outcome"] == "error"
    assert "postgres unreachable" in last["error"]


def test_a_rejected_explanation_marks_the_portfolio_failed_and_exits_one(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("saved", 7, 3, {}))

    def rejected(*args):
        raise ExplanationRejected("HYNIX: missing")

    monkeypatch.setattr(entry, "explain", rejected)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    events = _events(capsys.readouterr().out)
    failed = next(e for e in events if e["message"] == "explain_failed")
    assert failed["level"] == "ERROR"
    assert events[-1]["outcome"] == "explanation_failed"
    assert events[-1]["level"] == "ERROR"
    assert "HYNIX: missing" in events[-1]["error"]
    assert env == {"trace": 7, "failed": 7}


def test_provider_error_during_explain_marks_the_portfolio_failed_and_exits_one(
    env, monkeypatch, capsys
):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("saved", 7, 3, {}))

    def provider_error(*args):
        raise RuntimeError("502 from provider")

    monkeypatch.setattr(entry, "explain", provider_error)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    events = _events(capsys.readouterr().out)
    failed = next(e for e in events if e["message"] == "explain_failed")
    assert failed["level"] == "ERROR"
    assert events[-1]["outcome"] == "explanation_failed"
    assert events[-1]["level"] == "ERROR"
    assert "502 from provider" in events[-1]["error"]
    assert env == {"trace": 7, "failed": 7}


def test_no_explanation_without_a_saved_portfolio(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("max_turns", None, 150, {}))

    with pytest.raises(SystemExit):
        entry.main()

    assert env == {}


def test_an_unexplained_portfolio_is_explained_again_without_the_agent(env, monkeypatch, capsys):
    trace = [TraceEntry(turn=1, kind="model", text="삼성 사요")]
    monkeypatch.setattr(entry, "load_unexplained", lambda engine: (5, trace))
    monkeypatch.setattr(entry, "load_briefing", lambda engine, days: pytest.fail("briefed"))
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: pytest.fail("agent ran"))
    seen = {}
    monkeypatch.setattr(
        entry,
        "explain",
        lambda model, targets, t, chars: seen.update(trace=t) or Explanations(stocks=[]),
    )

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    assert seen["trace"] == trace
    assert env == {"saved": 5}
    events = _events(capsys.readouterr().out)
    assert [e["message"] for e in events] == ["run_start", "reexplain", "explained", "run_end"]
    assert (events[-1]["outcome"], events[-1]["portfolio_id"]) == ("reexplained", 5)


def test_a_failed_reexplanation_stays_failed_and_exits_one(env, monkeypatch, capsys):
    monkeypatch.setattr(entry, "load_unexplained", lambda engine: (5, []))

    def rejected(*args):
        raise ExplanationRejected("HYNIX: missing")

    monkeypatch.setattr(entry, "explain", rejected)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    assert env == {"failed": 5}
    assert _events(capsys.readouterr().out)[-1]["outcome"] == "explanation_failed"
