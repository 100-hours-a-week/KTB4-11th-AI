import json

import pytest
import talib
from portfolio_builder import __main__ as entry
from portfolio_builder import builder, submitters
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
    monkeypatch.setattr(
        submitters, "save_trace", lambda engine, pid, trace: calls.update(trace=pid)
    )
    monkeypatch.setattr(entry, "load_unexplained", lambda engine: None)
    monkeypatch.setattr(entry, "load_targets", lambda engine, pid: [])
    monkeypatch.setattr(entry, "explain", lambda *args: entry.Explanations(stocks=[]))
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

    monkeypatch.setattr(builder, "run_agent", fake_run_agent)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    out = capsys.readouterr().out
    events = _events(out)
    messages = [e["message"] for e in events]
    assert messages == ["run_start", "ingestion", "prompt", "explained", "run_end"]
    assert events[0]["logger"] == "portfolio_builder.__main__"
    assert events[0]["provider"] == "openrouter"
    assert events[-1]["turns"] == 3
    assert events[-1]["usage"] == {"input": 10}
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
    monkeypatch.setattr(
        builder, "run_agent", lambda **kwargs: RunResult("max_turns", None, 150, {})
    )

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 1
    assert _events(capsys.readouterr().out)[-1]["level"] == "ERROR"


def test_a_failure_before_the_agent_raises_after_run_end(env, monkeypatch, capsys):
    from unittest.mock import Mock

    engine = Mock()
    monkeypatch.setattr(entry.sa, "create_engine", lambda dsn: engine)

    def broken(engine, days):
        raise RuntimeError("postgres unreachable")

    monkeypatch.setattr(entry, "load_briefing", lambda engine, client, days: broken(engine, days))

    with pytest.raises(RuntimeError):
        entry.main()

    last = _events(capsys.readouterr().out)[-1]
    assert last["message"] == "run_end"
    assert last["outcome"] == "error"
    assert "postgres unreachable" in last["error"]
    engine.dispose.assert_called_once()


def test_a_rejected_explanation_marks_the_portfolio_failed_and_exits_one(env, monkeypatch, capsys):
    monkeypatch.setattr(builder, "run_agent", lambda **kwargs: RunResult("saved", 7, 3, {}))

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
    monkeypatch.setattr(builder, "run_agent", lambda **kwargs: RunResult("saved", 7, 3, {}))

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
    monkeypatch.setattr(
        builder, "run_agent", lambda **kwargs: RunResult("max_turns", None, 150, {})
    )

    with pytest.raises(SystemExit):
        entry.main()

    assert env == {}


def test_an_unexplained_portfolio_is_explained_again_without_the_agent(env, monkeypatch, capsys):
    trace = [TraceEntry(turn=1, kind="model", text="삼성 사요")]
    monkeypatch.setattr(entry, "load_unexplained", lambda engine: (5, trace))
    monkeypatch.setattr(entry, "load_briefing", lambda engine, client, days: pytest.fail("briefed"))
    monkeypatch.setattr(builder, "run_agent", lambda **kwargs: pytest.fail("agent ran"))
    seen = {}
    monkeypatch.setattr(
        entry,
        "explain",
        lambda explainer, targets, t, chars: seen.update(trace=t) or entry.Explanations(stocks=[]),
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


def test_default_deployment_without_disclosure_config_keeps_portfolio_generation(env, monkeypatch):
    monkeypatch.delenv("DISCLOSURE_ENABLED", raising=False)
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_BASE_URI")
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_MODEL")
    monkeypatch.setattr(
        builder, "DisclosureSettings", lambda: pytest.fail("disabled model settings loaded")
    )
    captured = {}

    def run(**kwargs):
        captured.update(kwargs)
        return RunResult("saved", 7, 3, {})

    monkeypatch.setattr(builder, "run_agent", run)
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
        builder, "run_agent", lambda **kwargs: pytest.fail("agent ran without model config")
    )
    with pytest.raises(ValidationError):
        entry.main()


@pytest.fixture
def participant_mode(env, monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_ENABLED", "true")
    monkeypatch.setenv("PORTFOLIO_BUILDER_BACKEND_BASE_URI", "https://backend")
    monkeypatch.setenv("PORTFOLIO_BUILDER_BACKEND_JWT_SECRET", "s" * 32)
    monkeypatch.setenv("PORTFOLIO_BUILDER_BACKEND_JWT_ISSUER", "issuer")
    run = Mock(return_value=True)
    monkeypatch.setattr(entry, "run_participants", run, raising=False)
    return run


def test_competition_mode_runs_only_participant_portfolios(participant_mode, monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("competition mode also generated a common portfolio")

    monkeypatch.setattr(builder, "run_agent", unexpected)
    with pytest.raises(SystemExit) as stopped:
        entry.main()
    assert stopped.value.code == 0
    participant_mode.assert_called_once()


def test_failed_participant_mode_exits_nonzero(participant_mode):
    participant_mode.return_value = False
    with pytest.raises(SystemExit) as stopped:
        entry.main()
    assert stopped.value.code == 1


def test_common_mode_does_not_query_backend_accounts(env, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BUILDER_COMPETITION_ENABLED", "false")
    monkeypatch.setattr(entry, "run_participants", lambda *args: pytest.fail("queried backend"))
    monkeypatch.setattr(builder, "run_agent", lambda **kwargs: RunResult("saved", 7, 1, {}))
    with pytest.raises(SystemExit) as stopped:
        entry.main()
    assert stopped.value.code == 0


@pytest.mark.parametrize("participant_id", [None, 11])
def test_shared_generation_selects_only_the_submit_schema_and_prompt(
    env, monkeypatch, participant_id
):
    from unittest.mock import Mock

    from portfolio_builder.agent.system_prompt import SYSTEM_PROMPT
    from portfolio_builder.competition.prompt import COMPETITION_SYSTEM_PROMPT

    monkeypatch.setenv("DISCLOSURE_ENABLED", "false")
    captured = {}
    result = RunResult("max_turns", None, 1, {})
    monkeypatch.setattr(builder, "run_agent", lambda **kwargs: captured.update(kwargs) or result)
    settings = builder.Settings()
    assert (
        builder._generate(
            Mock(),
            settings,
            Mock(),
            Mock(),
            Mock(),
            frozenset(),
            frozenset(),
            "brief",
            participant_id,
            Mock() if participant_id is not None else None,
        )
        is result
    )
    submit = next(tool for tool in captured["tools"] if tool.name == "submit_portfolio")
    assert ("constraints" in submit.args) is (participant_id is not None)
    assert captured["system_prompt"] == (
        SYSTEM_PROMPT if participant_id is None else COMPETITION_SYSTEM_PROMPT
    )


def test_evaluation_mode_dumps_the_submission_without_database_writes(
    env, monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv("PORTFOLIO_BUILDER_SUBMITTER", "evaluation")
    monkeypatch.setenv("PORTFOLIO_BUILDER_EVALUATION_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(entry, "load_unexplained", lambda engine: pytest.fail("read retry queue"))
    monkeypatch.setattr(entry, "explain", lambda *args: pytest.fail("explained"))
    trace = [TraceEntry(turn=1, kind="model", text="삼성 사요")]

    def fake_run_agent(**kwargs):
        [submit] = [t for t in kwargs["tools"] if t.name == "submit_portfolio"]
        args = {
            "holdings": [{"company_id": "00126380", "weight": 1, "reason": "HBM",
                          "cited_cluster_ids": []}],
            "exits": [],
            "cash_weight": 1,
            "commentary": "c",
        }  # fmt: skip
        command = submit.invoke(
            {"name": "submit_portfolio", "args": args, "id": "c1", "type": "tool_call"}
        )
        return RunResult("saved", command.update["portfolio_id"], 1, {}, trace=trace)

    monkeypatch.setattr(builder, "run_agent", fake_run_agent)

    with pytest.raises(SystemExit) as exit_:
        entry.main()

    assert exit_.value.code == 0
    assert env == {}
    submission = json.loads((tmp_path / "submission.json").read_text())
    assert submission["model"] == "openrouter/openai/gpt-5.5"
    assert submission["cash_weight"] == 0.5
    assert submission["holdings"][0]["weight"] == 0.5
    assert json.loads((tmp_path / "trace.json").read_text())[0]["text"] == "삼성 사요"
    assert _events(capsys.readouterr().out)[-1]["outcome"] == "saved"
