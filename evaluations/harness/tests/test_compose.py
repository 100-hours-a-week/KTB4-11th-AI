import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
LAYERS = {
    "none": ["information.none.yaml"],
    "market": ["information.market.yaml"],
    "raw": ["information.market.yaml", "information.news.yaml"],
    "enriched": ["information.market.yaml", "information.news.yaml", "information.features.yaml"],
    "disclosures": [
        "information.market.yaml",
        "information.news.yaml",
        "information.disclosures.yaml",
    ],
}
SERVICES = {
    "none": set(),
    "market": {"market-http-pit"},
    "raw": {"market-http-pit", "news-http-pit"},
    "enriched": {"market-http-pit", "news-http-pit", "features"},
    "disclosures": {"market-http-pit", "news-http-pit", "disclosure-http-pit"},
}
DECISIONS = {"rule": "decision.rule.yaml", "docker-agent": "decision.docker-agent.yaml"}
ENV = {
    "EVALUATION_SPEC": "/tmp/spec.json",
    "EVALUATION_RUN_ID": "r1",
    "EVALUATION_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@postgres:5432/ktb",
    "EVALUATION_DECISION": "equal-weight",
    "DOCKER_AGENT_URL": "https://example.com/docker-agent",
    "DOCKER_AGENT_SHA256": "0" * 64,
    "EVALUATION_MODEL_PROVIDER": "openrouter",
    "EVALUATION_MODEL": "vendor/model",
    "EVALUATION_MODEL_API_KEY_ENV": "OPENROUTER_API_KEY",
    "EVALUATION_MODEL_API_KEY_FILE": "/tmp/model-key",
}

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")


def render(information: str, decision: str, extra: dict[str, str] | None = None) -> dict:
    files = ["compose.evaluate.yaml", *(f"evaluations/compose/{f}" for f in LAYERS[information])]
    files.append(f"evaluations/compose/{DECISIONS[decision]}")
    command = ["docker", "compose", "-p", "check"]
    for file in files:
        command += ["-f", file]
    done = subprocess.run(
        [*command, "config", "--format", "json"],
        cwd=ROOT,
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", "/tmp"),
            **ENV,
            **(extra or {}),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode and "unknown" in done.stderr and "format" in done.stderr:
        pytest.skip("docker compose does not support config --format json")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


COMBINATIONS = [(i, d) for i in LAYERS for d in DECISIONS]


@pytest.mark.parametrize(("information", "decision"), COMBINATIONS)
def test_each_combination_runs_only_its_services(information, decision):
    config = render(information, decision)
    services = config["services"]
    expected = {"market-http-prices", "runner"} | SERVICES[information]
    if decision == "docker-agent":
        expected.add("agent-worker")
    assert set(services) == expected
    for name, service in services.items():
        assert set(service.get("depends_on", {})) <= set(services), name
    runner = services["runner"]["environment"]
    assert runner["HARNESS_INFORMATION_VARIANT"] == information
    expected_decision = "docker-agent" if decision == "docker-agent" else "equal-weight"
    assert runner["HARNESS_DECISION_SYSTEM"] == expected_decision


@pytest.mark.parametrize(("information", "decision"), COMBINATIONS)
def test_decision_containers_cannot_reach_data_or_harness_prices(information, decision):
    config = render(information, decision)
    services = config["services"]
    networks = config["networks"]
    for name in ("harness", "information", "decision"):
        if name in networks:
            assert networks[name].get("internal") is True, name
    assert set(services["market-http-prices"]["networks"]) == {"dataset", "harness"}
    if "agent-worker" in services:
        agent = services["agent-worker"]
        assert set(agent["networks"]) == {"information", "decision", "egress"}
        assert not any(v["target"] == "/clock" for v in agent.get("volumes", []))
        assert "/run/secrets/model_api_key" == agent["environment"]["HARNESS_AGENT_API_KEY_FILE"]
        assert not any("sk-" in str(value) for value in agent["environment"].values())
    if "features" in services:
        assert set(services["features"]["networks"]) == {"information"}
    if "disclosure-http-pit" in services:
        assert set(services["disclosure-http-pit"]["networks"]) == {
            "dataset",
            "information",
            "egress",
        }


@pytest.mark.parametrize("information", ["market", "raw", "enriched", "disclosures"])
def test_only_pit_servers_read_the_clock_and_only_the_runner_writes_it(information):
    services = render(information, "rule")["services"]
    for name, service in services.items():
        mounts = {v["target"]: v.get("read_only", False) for v in service.get("volumes", [])}
        if name == "runner":
            assert mounts["/clock"] is False
        elif name.endswith("-pit"):
            assert mounts["/clock"] is True
            assert service["environment"].get("MARKET_HTTP_EVALUATION_ACCESS", "pit") == "pit"
        else:
            assert "/clock" not in mounts, name


PUBLIC_URLS = {
    "market-http-pit": "MARKET_HTTP_EVALUATION_PUBLIC_URL",
    "news-http-pit": "NEWS_HTTP_EVALUATION_PUBLIC_URL",
    "features": "HARNESS_FEATURES_PUBLIC_URL",
    "disclosure-http-pit": "DISCLOSURE_HTTP_PUBLIC_URL",
}


@pytest.mark.parametrize("information", ["market", "raw", "enriched", "disclosures"])
def test_tool_servers_publish_their_compose_address(information):
    services = render(information, "docker-agent")["services"]
    for name, variable in PUBLIC_URLS.items():
        if name in services:
            assert services[name]["environment"][variable] == f"http://{name}:8000"
    assert services["runner"]["environment"]["HARNESS_SUBMISSION_URL"] == "http://runner:8100"


def test_the_agent_worker_runs_as_the_secret_owner():
    agent = render("raw", "docker-agent")["services"]["agent-worker"]
    assert agent["user"] == "10001:10001"
    owner = render("raw", "docker-agent", {"EVALUATION_UID": "1234", "EVALUATION_GID": "99"})
    assert owner["services"]["agent-worker"]["user"] == "1234:99"


def test_disclosure_model_identity_matches_production_and_takes_overrides():
    production = yaml.safe_load((ROOT / "compose.prod.yaml").read_text())
    identity = production["x-disclosure-model-environment"]
    services = render("disclosures", "rule")["services"]
    environment = services["disclosure-http-pit"]["environment"]
    for name, value in identity.items():
        assert environment[name] == value.split(":-", 1)[1].rstrip("}"), name
    custom = render("disclosures", "rule", {"DISCLOSURE_EMBEDDING_ADAPTER_SHA256": "f" * 64})
    pit = custom["services"]["disclosure-http-pit"]["environment"]
    assert pit["DISCLOSURE_EMBEDDING_ADAPTER_SHA256"] == "f" * 64
