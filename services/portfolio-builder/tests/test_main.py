import json

import pytest
import talib
from portfolio_builder import __main__ as entry
from portfolio_builder.agent.run import RunResult
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.briefing import Briefing
from portfolio_builder.explain import ExplanationRejected

REQUIRED = {
    "PORTFOLIO_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/ktb",
    "PORTFOLIO_BUILDER_MARKET_HTTP_BASE_URI": "http://market-http:8000",
    "PORTFOLIO_BUILDER_NEWS_HTTP_BASE_URI": "http://news-http:8000",
    "PORTFOLIO_BUILDER_LLM_API_KEY": "test-openrouter-key",
    "PORTFOLIO_BUILDER_LLM_MODEL": "openai/gpt-5.5",
}
BRIEFING = Briefing(None, frozenset(), 0, 0, [1], 1, 1, "brief")


def test_ta_lib_is_importable():
    assert "ROCP" in talib.get_functions()


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DISCLOSURE_ENABLED", "true")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://unused/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "test-model")
    monkeypatch.setattr(entry, "NewsClient", lambda uri: object())
    monkeypatch.setattr(entry, "load_briefing", lambda engine, client, days: BRIEFING)
    calls = {}
    monkeypatch.setattr(entry, "save_trace", lambda engine, pid, trace: calls.update(trace=pid))
    monkeypatch.setattr(entry, "load_unexplained", lambda engine: None)
    monkeypatch.setattr(
        entry,
        "explain_and_save",
        lambda engine, explainer, pid, trace, chars: calls.update(saved=pid) or 0,
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
        "search_disclosures",
        "get_disclosure_sections",
        "get_disclosure_financials",
        "search_graph",
        "find_graph_paths",
        "analyze_technicals",
        "submit_portfolio",
    }
    assert captured["max_turns"] == 150
    assert captured["disclosure_enabled"] is True
    assert "search_disclosures" in captured["system_prompt"]
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

    monkeypatch.setattr(entry, "load_briefing", lambda engine, client, days: broken(engine, days))

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

    monkeypatch.setattr(entry, "explain_and_save", rejected)

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

    monkeypatch.setattr(entry, "explain_and_save", provider_error)

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
    monkeypatch.setattr(entry, "load_briefing", lambda engine, client, days: pytest.fail("briefed"))
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: pytest.fail("agent ran"))
    seen = {}
    monkeypatch.setattr(
        entry,
        "explain_and_save",
        lambda engine, explainer, pid, t, chars: seen.update(trace=t) or env.update(saved=pid) or 0,
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

    monkeypatch.setattr(entry, "explain_and_save", rejected)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    assert env == {"failed": 5}
    assert _events(capsys.readouterr().out)[-1]["outcome"] == "explanation_failed"


def test_default_deployment_without_disclosure_config_keeps_portfolio_generation(env, monkeypatch):
    monkeypatch.delenv("DISCLOSURE_ENABLED", raising=False)
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_BASE_URI")
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_MODEL")
    monkeypatch.setattr(
        entry, "DisclosureSettings", lambda: pytest.fail("disabled model settings loaded")
    )
    captured = {}

    def run(**kwargs):
        captured.update(kwargs)
        return RunResult("saved", 7, 3, {})

    monkeypatch.setattr(entry, "run_agent", run)
    with pytest.raises(SystemExit) as result:
        entry.main()
    assert result.value.code == 0
    assert captured["disclosure_enabled"] is False
    assert not any("disclosure" in tool.name for tool in captured["tools"])
    assert "search_disclosures" not in captured["system_prompt"]
    assert env == {"trace": 7, "saved": 7}


def test_enabled_disclosure_with_missing_endpoint_fails_before_agent(env, monkeypatch):
    from pydantic import ValidationError

    monkeypatch.setenv("DISCLOSURE_ENABLED", "true")
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_BASE_URI")
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_MODEL")
    monkeypatch.setattr(
        entry, "run_agent", lambda **kwargs: pytest.fail("agent ran without model config")
    )
    with pytest.raises(ValidationError):
        entry.main()


@pytest.fixture
def participant_mode(env, monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_SCHEDULED_ENABLED", "true")
    monkeypatch.setenv("PORTFOLIO_BUILDER_BACKEND_BASE_URI", "https://backend")
    monkeypatch.setenv("PORTFOLIO_BUILDER_BACKEND_JWT_SECRET", "s" * 32)
    monkeypatch.setenv("PORTFOLIO_BUILDER_BACKEND_JWT_ISSUER", "issuer")
    scheduled = Mock(return_value=True)
    monkeypatch.setattr(entry, "run_scheduled", scheduled, raising=False)
    return scheduled


def test_scheduled_participants_run_after_a_successful_common_run(participant_mode, monkeypatch):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("saved", 7, 1, {}))

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    participant_mode.assert_called_once()


def test_scheduled_participants_run_even_if_common_generation_fails(participant_mode, monkeypatch):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("max_turns", None, 1, {}))

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    participant_mode.assert_called_once()


def test_scheduled_failure_makes_the_overall_run_fail(participant_mode, monkeypatch):
    monkeypatch.setattr(entry, "run_agent", lambda **kwargs: RunResult("saved", 7, 1, {}))
    participant_mode.return_value = False

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1


def test_scheduled_participants_run_after_a_common_exception(participant_mode, monkeypatch):
    def failing_briefing(*args):
        raise RuntimeError("common failed")

    monkeypatch.setattr(entry, "load_briefing", failing_briefing)

    with pytest.raises(RuntimeError, match="common failed"):
        entry.main()

    participant_mode.assert_called_once()
