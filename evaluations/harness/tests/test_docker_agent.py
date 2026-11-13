import json
import stat
from datetime import date, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from harness.agent_worker import RunRequest, agent_config, create_worker_app, run_agent
from harness.contracts import DailyBar, PortfolioProposal
from harness.docker_agent import DockerAgent
from harness.information import MARKET, NEWS, HttpInformationProvider
from harness.loop import EpisodeSpec, run_episode
from harness.memory import InMemoryPriceSource, MemoryClock
from harness.settings import AgentWorkerSettings
from harness.submission import SubmissionDesk
from ktb_core.krx import KST

UNIVERSE = ("000660", "005930")
VALID = {"holdings": [{"code": "005930", "weight": 1}], "cash_weight": 0}
INVALID = {"holdings": [{"code": "999999", "weight": 1}], "cash_weight": 0}
ENDPOINTS = {MARKET: "http://market-pit:8000", NEWS: "http://news-pit:8000"}


def at(day, hour=16):
    return datetime(2026, 7, day, hour, tzinfo=KST)


WEEK = (3, 6, 7, 8, 9, 10)
PRICES = InMemoryPriceSource(
    {s: [DailyBar(date(2026, 7, d), 100.0, 100, 100, 100, 1) for d in WEEK] for s in UNIVERSE}
)
SPEC = EpisodeSpec(UNIVERSE, 10_000, (at(6),), at(10), 0.0, 0.0)


def specs(servers=True):
    def handler(request):
        spec = {"paths": {"/x": {"get": {"operationId": request.url.host}}}}
        if servers:
            spec["servers"] = [{"url": f"http://{request.url.netloc.decode()}"}]
        return httpx.Response(200, json=spec)

    return httpx.Client(transport=httpx.MockTransport(handler))


def agent(desk, behaviour, seen=None, http=None):
    def handler(request):
        if request.url.path == "/manifest":
            return httpx.Response(200, json={"name": "docker-agent", "model": "m"})
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        result = {"exit_code": 0, "timed_out": False, "duration_seconds": 1.0, "stdout": "done",
                  "stderr": "", "config_sha256": "x"}  # fmt: skip
        for step in behaviour:
            if step == "raise":
                raise httpx.ConnectError("worker down")
            if isinstance(step, dict) and "exit_code" in step:
                result.update(step)
            else:
                desk.submit(body["decision_id"], step)
        return httpx.Response(200, json=result)

    client = httpx.Client(base_url="http://worker", transport=httpx.MockTransport(handler))
    return DockerAgent(client, desk, "http://runner:8100", 30, http or specs())


def run(decision_system):
    information = HttpInformationProvider("raw", ENDPOINTS, specs(), "1")
    return run_episode(SPEC, decision_system, information, PRICES, MemoryClock())


def test_accepted_submission_becomes_the_proposal():
    seen = []
    desk = SubmissionDesk()
    episode = run(agent(desk, [VALID], seen))
    decision = episode.decisions[0]
    assert decision.action == "rebalance"
    assert decision.proposal == PortfolioProposal({"005930": 1.0}, 0.0)
    assert seen[0]["openapi"] == [
        "http://market-pit:8000/openapi.json",
        "http://news-pit:8000/openapi.json",
        "http://runner:8100/openapi.json",
    ]
    assert "Decision time: 2026-07-06T16:00+09:00" in seen[0]["prompt"]
    assert decision.audit["agent"]["stdout"] == "done"
    assert [s["status"] for s in decision.audit["submissions"]] == ["accepted"]
    assert {f.symbol for f in episode.fills} == {"005930"}


def test_duplicate_submission_keeps_the_first_and_is_audited():
    episode = run(agent(SubmissionDesk(), [VALID, {**VALID, "cash_weight": 5}]))
    decision = episode.decisions[0]
    assert decision.proposal == PortfolioProposal({"005930": 1.0}, 0.0)
    assert [s["status"] for s in decision.audit["submissions"]] == ["accepted", "duplicate"]


@pytest.mark.parametrize(
    ("behaviour", "kind"),
    [
        ([], "no_submission"),
        ([INVALID], "invalid_submission"),
        ([{"exit_code": None, "timed_out": True}], "timeout"),
        ([{"exit_code": 1}], "tool_error"),
        (["raise"], "tool_error"),
    ],
)
def test_failures_are_never_success(behaviour, kind):
    desk = SubmissionDesk()
    episode = run(agent(desk, behaviour))
    decision = episode.decisions[0]
    assert decision.action == "failed"
    assert decision.failure.kind == kind
    assert episode.fills == ()
    with pytest.raises(RuntimeError):
        desk.close()


def test_invalid_then_valid_submission_succeeds():
    episode = run(agent(SubmissionDesk(), [INVALID, VALID]))
    assert episode.decisions[0].action == "rebalance"


def test_manifest_comes_from_the_worker_and_the_desk_spec():
    manifest = agent(SubmissionDesk(), []).manifest
    assert manifest["model"] == "m"
    assert len(manifest["prompt_sha256"]) == 64
    assert manifest["submission"]["server"] == "http://runner:8100"


def test_a_desk_spec_without_servers_is_an_error():
    docker_agent = agent(SubmissionDesk(), [], http=specs(servers=False))
    with pytest.raises(ValueError, match="no servers entry"):
        dict(docker_agent.manifest)


@pytest.fixture
def fake_binary(tmp_path):
    script = tmp_path / "docker-agent"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = version ]; then echo "docker-agent v9"; exit 0; fi\n'
        'case "$5" in *sleep*) sleep 5;; esac\n'
        'echo "decision=$HARNESS_DECISION_ID key=$OPENROUTER_API_KEY"\n'
        'cat "$4"\n'
        'case "$5" in *fail*) exit 3;; esac\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture
def settings(tmp_path, fake_binary, monkeypatch):
    key = tmp_path / "key"
    key.write_text("secret-value\n")
    monkeypatch.setenv("HARNESS_AGENT_BINARY", str(fake_binary))
    monkeypatch.setenv("HARNESS_AGENT_MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("HARNESS_AGENT_MODEL", "vendor/model")
    monkeypatch.setenv("HARNESS_AGENT_API_KEY_FILE", str(key))
    monkeypatch.setenv("HARNESS_AGENT_API_KEY_ENV", "OPENROUTER_API_KEY")
    monkeypatch.setenv("HARNESS_AGENT_OUTPUT_CHARS", "4000")
    return AgentWorkerSettings()


OPENAPI = ["http://market-http-pit:8000/openapi.json", "http://runner:8100/openapi.json"]


def request(prompt="go", timeout=10.0):
    return RunRequest(decision_id="abc", prompt=prompt, openapi=OPENAPI, timeout_seconds=timeout)


def test_agent_config_renders_openapi_toolsets(settings):
    config = agent_config(settings, "be careful", OPENAPI)
    root = config["agents"]["root"]
    assert config["models"]["decision"] == {
        "provider": "openrouter",
        "model": "vendor/model",
        "token_key": "OPENROUTER_API_KEY",
    }
    assert root["instruction"] == "be careful"
    assert root["toolsets"] == [
        {
            "type": "openapi",
            "url": url,
            "headers": {"X-Decision-Id": "${env.HARNESS_DECISION_ID}"},
            "allow_private_ips": True,
        }
        for url in OPENAPI
    ]


def test_run_requests_need_absolute_spec_urls():
    with pytest.raises(ValueError):
        RunRequest(decision_id="a", prompt="p", openapi=["/openapi.json"], timeout_seconds=1)
    with pytest.raises(ValueError):
        RunRequest(decision_id="a", prompt="p", openapi=[], timeout_seconds=1)


def test_worker_runs_the_binary_with_a_fresh_home_and_the_key(settings):
    result = run_agent(settings, "instruction", request(), "secret-value")
    assert result.exit_code == 0
    assert not result.timed_out
    first, rest = result.stdout.split("\n", 1)
    assert first == "decision=abc key=secret-value"
    assert json.loads(rest)["agents"]["root"]["toolsets"][1]["url"] == OPENAPI[1]
    assert "secret-value" not in json.dumps(agent_config(settings, "i", OPENAPI))


def test_worker_reports_exit_codes_and_timeouts(settings):
    assert run_agent(settings, "i", request("please fail"), None).exit_code == 3
    slow = run_agent(settings, "i", request("sleep", timeout=0.5), None)
    assert slow.timed_out and slow.exit_code is None


def test_worker_app_serves_manifest_and_runs(settings):
    client = TestClient(create_worker_app(settings))
    manifest = client.get("/manifest").json()
    assert manifest["binary_version"] == "docker-agent v9"
    assert manifest["model"] == "vendor/model"
    response = client.post("/runs", json=request().model_dump())
    assert response.json()["exit_code"] == 0
    assert (
        client.post("/runs", json={**request().model_dump(), "decision_id": "a b"}).status_code
        == 422
    )


def test_settings_pair_key_file_and_env(monkeypatch):
    monkeypatch.setenv("HARNESS_AGENT_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("HARNESS_AGENT_MODEL", "m")
    monkeypatch.setenv("HARNESS_AGENT_API_KEY_FILE", "/run/secrets/key")
    with pytest.raises(ValueError):
        AgentWorkerSettings()


def test_worker_reads_the_key_at_startup(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "api_key_file", str(tmp_path / "missing"))
    with pytest.raises(OSError):
        create_worker_app(settings)
    empty = tmp_path / "empty"
    empty.write_text("\n")
    monkeypatch.setattr(settings, "api_key_file", str(empty))
    with pytest.raises(ValueError, match="empty"):
        create_worker_app(settings)
