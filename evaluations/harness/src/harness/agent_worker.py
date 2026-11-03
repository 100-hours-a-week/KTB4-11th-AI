import hashlib
import json
import os
import shlex
import subprocess
import tempfile
from importlib.resources import files
from pathlib import Path
from time import monotonic

import uvicorn
from fastapi import FastAPI
from ktb_core.logging import setup_logging
from pydantic import BaseModel, Field

from harness.contracts import HttpTool
from harness.settings import AgentWorkerSettings

DECISION_ENV = "HARNESS_DECISION_ID"


class ToolIn(BaseModel):
    name: str
    description: str
    method: str
    url: str
    args: dict[str, dict[str, object]] = {}
    required: list[str] = []


class RunRequest(BaseModel):
    decision_id: str = Field(pattern=r"^[A-Za-z0-9-]+$")
    prompt: str
    tools: list[ToolIn]
    timeout_seconds: float = Field(gt=0)


class RunResult(BaseModel):
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout: str
    stderr: str
    config_sha256: str


def instruction_text(settings: AgentWorkerSettings) -> str:
    if settings.instruction_file:
        return Path(settings.instruction_file).read_text()
    return files("harness").joinpath("agent_instruction.md").read_text()


def agent_config(
    settings: AgentWorkerSettings, instruction: str, tools: list[ToolIn] | list[HttpTool]
) -> dict[str, object]:
    model: dict[str, object] = {"provider": settings.model_provider, "model": settings.model}
    if settings.model_base_url:
        model["base_url"] = settings.model_base_url
    if settings.max_tokens:
        model["max_tokens"] = settings.max_tokens
    if settings.api_key_env:
        model["token_key"] = settings.api_key_env
    toolsets = []
    for tool in tools:
        headers = {"X-Decision-Id": "${env." + DECISION_ENV + "}"}
        if tool.method == "POST":
            headers["Content-Type"] = "application/json"
        toolsets.append(
            {
                "type": "api",
                "allow_private_ips": True,
                "api_config": {
                    "name": tool.name,
                    "method": tool.method,
                    "endpoint": tool.url,
                    "instruction": tool.description,
                    "args": dict(tool.args),
                    "required": list(tool.required),
                    "headers": headers,
                },
            }
        )
    return {
        "models": {"decision": model},
        "agents": {
            "root": {
                "model": "decision",
                "description": "Portfolio decision agent under evaluation",
                "instruction": instruction,
                "toolsets": toolsets,
            }
        },
    }


def _text(output: str | bytes | None, limit: int) -> str:
    text = output.decode(errors="replace") if isinstance(output, bytes) else output or ""
    return text if len(text) <= limit else text[:limit] + f"\n[truncated {len(text) - limit}]"


def run_agent(settings: AgentWorkerSettings, instruction: str, request: RunRequest) -> RunResult:
    config = json.dumps(agent_config(settings, instruction, request.tools), ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix="agent-") as home:
        path = Path(home) / "agent.yaml"
        path.write_text(config)
        env = {**os.environ, "HOME": home, DECISION_ENV: request.decision_id}
        if settings.api_key_file and settings.api_key_env:
            env[settings.api_key_env] = Path(settings.api_key_file).read_text().strip()
        command = [settings.binary, *shlex.split(settings.run_args), str(path), request.prompt]
        started = monotonic()
        try:
            done = subprocess.run(
                command,
                cwd=home,
                env=env,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
            )
            exit_code, timed_out, stdout, stderr = done.returncode, False, done.stdout, done.stderr
        except subprocess.TimeoutExpired as expired:
            exit_code, timed_out = None, True
            stdout, stderr = expired.stdout, expired.stderr
    return RunResult(
        exit_code=exit_code,
        timed_out=timed_out,
        duration_seconds=monotonic() - started,
        stdout=_text(stdout, settings.output_chars),
        stderr=_text(stderr, settings.output_chars),
        config_sha256=hashlib.sha256(config.encode()).hexdigest(),
    )


def _binary_version(settings: AgentWorkerSettings) -> str:
    try:
        done = subprocess.run(
            [settings.binary, "version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"unknown ({type(error).__name__})"
    return (done.stdout or done.stderr).strip() or f"unknown (exit {done.returncode})"


def create_worker_app(settings: AgentWorkerSettings) -> FastAPI:
    instruction = instruction_text(settings)
    manifest: dict[str, object] = {
        "name": "docker-agent",
        "binary_version": _binary_version(settings),
        "run_args": settings.run_args,
        "model_provider": settings.model_provider,
        "model": settings.model,
        "model_base_url": settings.model_base_url,
        "max_tokens": settings.max_tokens,
        "instruction_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
    }
    app = FastAPI(title="harness-agent-worker", docs_url=None, redoc_url=None)

    @app.get("/manifest")
    def get_manifest() -> dict[str, object]:
        return manifest

    @app.post("/runs")
    def post_run(request: RunRequest) -> RunResult:
        return run_agent(settings, instruction, request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    settings = AgentWorkerSettings()
    setup_logging(settings.log_level, service_name="harness-agent-worker")
    uvicorn.run(
        create_worker_app(settings), host=settings.host, port=settings.port, log_config=None
    )
