import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

DOCKER = r"""
import json
import os
import pathlib
import shlex
import sys

args = sys.argv[1:]
state_path = pathlib.Path(os.environ["DOCKER_STATE"])
state = json.loads(state_path.read_text())
state["calls"].append(args)
status = 0
output = ""

def owned():
    return [key for key, value in state["containers"].items()
            if value["project"] == "ktb4-ai"
            and value["service"] == "competition-portfolio-builder"]

def image():
    paths = [args[i + 1] for i, arg in enumerate(args) if arg == "--env-file"]
    values = {}
    for name in paths:
        for line in pathlib.Path(name).read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = shlex.split(value)[0] if value else ""
    return values.get("APP_IMAGE", "old")

def worker(running):
    state["containers"]["worker-" + image()] = {
        "project": "ktb4-ai", "service": "competition-portfolio-builder",
        "image": image(), "running": running,
    }

if args[0] == "compose":
    operation = next(a for a in args[1:] if a in
                     ["config", "ps", "pull", "run", "up", "create"])
    if operation == "config":
        path = args[args.index("-f") + 1]
        config = json.loads(pathlib.Path(path).read_text())
        if "--services" in args:
            output = "\n".join(config["services"])
        elif "--format" in args:
            output = json.dumps(config)
    elif operation == "ps":
        output = "\n".join(owned()) if args[-1] == "competition-portfolio-builder" else ""
    elif operation == "run":
        if "competition-portfolio-builder" in args:
            if os.environ.get("FAIL_SETTINGS") == "1":
                status = 33
        elif "postgres-migrate" in args and os.environ.get("FAIL_MIGRATION") == "1":
            status = 17
    elif operation == "up" and "competition-portfolio-builder" in args:
        worker("--no-start" not in args)
        if image() == "new" and os.environ.get("FAIL_START") == "1":
            status = 41
        elif image() == "old" and os.environ.get("FAIL_RECOVERY") == "1":
            status = 42
    elif operation == "create":
        if "--no-deps" in args:
            raise SystemExit("unknown flag: --no-deps")
        worker(False)
elif args[0] == "info":
    output = "systemd 2"
elif args[0] == "ps":
    filters = [args[i + 1] for i, arg in enumerate(args) if arg == "--filter"]
    expected = {
        "label=com.docker.compose.project=ktb4-ai",
        "label=com.docker.compose.service=competition-portfolio-builder",
    }
    if set(filters) != expected:
        raise SystemExit("worker cleanup must filter project and service")
    output = "\n".join(owned())
elif args[0] == "inspect":
    keys = [a for a in args if a in state["containers"]]
    output = "\n".join(str(state["containers"][key]["running"]).lower() for key in keys)
elif args[0] == "stop":
    for key in args:
        if key in state["containers"]:
            state["containers"][key]["running"] = False
elif args[0] == "rm":
    for key in args:
        state["containers"].pop(key, None)

state_path.write_text(json.dumps(state))
if output:
    print(output)
raise SystemExit(status)
"""


@pytest.fixture(params=["cd-dev.yaml", "cd.yaml"])
def deploy(request, tmp_path):
    workflow = (ROOT / ".github/workflows" / request.param).read_text()
    upload, deployment = workflow.split("      - name: Deploy\n", 1)
    assert "scp scripts/competition-worker.sh" in upload
    assert ':~/ai/.schedule.next/competition-worker.sh"' in upload
    step = deployment.split("      - name:", 1)[0]
    script = textwrap.dedent(step.split("<<'REMOTE'\n", 1)[1].rsplit("\n          REMOTE", 1)[0])
    script = script.replace("cd ~/ai", 'cd "$DEPLOY_DIR"')
    stage = tmp_path / ".schedule.next"
    stage.mkdir()
    commands = tmp_path / "bin"
    commands.mkdir()
    state_path = tmp_path / "docker.json"
    for name in ["docker", "sudo", "systemctl", "install", "flock", "aws"]:
        path = commands / name
        if name == "docker":
            content = f"#!{sys.executable}\n" + DOCKER
        elif name == "systemctl":
            content = '#!/bin/sh\n[ "$1" != cat ] || exit 1\necho ktb-test.timer\n'
        else:
            content = "#!/bin/sh\nexit 0\n"
        path.write_text(content)
        path.chmod(0o755)
    script = script.replace("/usr/local/bin/aws", str(commands / "aws"))
    for pattern in ["ktb-*.service.in", "ktb-*.timer", "ktb-*.slice"]:
        for path in (ROOT / "infrastructure/systemd").glob(pattern):
            shutil.copy2(path, stage / path.name)
    for name in ["run-morning-pipeline.sh", "publish-scheduled-job-metric.sh"]:
        shutil.copy2(ROOT / "scripts" / name, stage / name)
        path = tmp_path / "scripts" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    helper = ROOT / "scripts/competition-worker.sh"
    if helper.exists():
        shutil.copy2(helper, stage / helper.name)
    (stage / "ssm_env.py").write_text(
        "import pathlib,sys\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_text('SETTING=new\\n')\n"
    )
    (stage / "base.env").write_text("SETTING=new\n")
    (tmp_path / ".image.env.next").write_text("APP_IMAGE=new\n")
    (tmp_path / "cleanup-images.sh.next").write_text("#!/bin/sh\nexit 0\n")
    services = {s: {} for s in ["postgres", "questdb", "news-http", "report-builder"]}
    (tmp_path / "compose.prod.yaml.next").write_text(
        json.dumps(
            {"name": "ktb4-ai", "services": {**services, "competition-portfolio-builder": {}}}
        )
    )

    def run(previous="running", **failures):
        previous_services = dict(services)
        if previous != "undefined":
            previous_services["competition-portfolio-builder"] = {}
        if previous != "fresh":
            (tmp_path / "compose.prod.yaml").write_text(
                json.dumps({"name": "ktb4-ai", "services": previous_services})
            )
            (tmp_path / ".image.env").write_text("APP_IMAGE=old\n")
            (tmp_path / ".env").write_text("SETTING=old\n")
        containers = {
            "unrelated": {
                "project": "another-project",
                "service": "competition-portfolio-builder",
                "image": "unrelated",
                "running": True,
            }
        }
        if previous in ["running", "stopped"]:
            containers["old-worker"] = {
                "project": "ktb4-ai",
                "service": "competition-portfolio-builder",
                "image": "old",
                "running": previous == "running",
            }
        state_path.write_text(json.dumps({"containers": containers, "calls": []}))
        result = subprocess.run(
            ["bash", "-se"],
            input=script,
            cwd=tmp_path,
            env={
                **os.environ,
                "PATH": str(commands) + os.pathsep + os.environ["PATH"],
                "DOCKER_STATE": str(state_path),
                "DEPLOY_DIR": str(tmp_path),
                **{name: str(value) for name, value in failures.items()},
            },
            text=True,
            capture_output=True,
        )
        state = json.loads(state_path.read_text())
        assert state["containers"]["unrelated"]["running"] is True
        return result, state

    return run


def workers(state):
    return [c for c in state["containers"].values() if c["project"] == "ktb4-ai"]


@pytest.mark.parametrize("previous", ["running", "stopped", "absent", "undefined", "fresh"])
def test_deploy_refreshes_worker_and_preserves_unrelated_containers(deploy, previous):
    result, state = deploy(previous)
    assert result.returncode == 0, result.stderr
    assert [(c["image"], c["running"]) for c in workers(state)] == [("new", True)]


def test_invalid_settings_leave_old_worker_running(deploy):
    result, state = deploy(FAIL_SETTINGS=1)
    assert result.returncode == 33, result.stderr
    assert [(c["image"], c["running"]) for c in workers(state)] == [("old", True)]
    assert not any(call[0] == "stop" and "old-worker" in call for call in state["calls"])


@pytest.mark.parametrize("previous", ["running", "stopped", "absent", "undefined", "fresh"])
def test_failed_replacement_restores_previous_worker_state(deploy, previous):
    result, state = deploy(previous, FAIL_START=1)
    assert result.returncode == 41, result.stderr
    expected = [("old", previous == "running")] if previous in ["running", "stopped"] else []
    assert [(c["image"], c["running"]) for c in workers(state)] == expected


def test_failure_before_replacement_preserves_old_worker(deploy):
    result, state = deploy(FAIL_MIGRATION=1)
    assert result.returncode == 17, result.stderr
    assert [(c["image"], c["running"]) for c in workers(state)] == [("old", True)]


def test_failed_recovery_keeps_original_failure_status(deploy):
    result, _ = deploy(FAIL_START=1, FAIL_RECOVERY=1)
    assert result.returncode == 41, result.stderr
    assert "competition-portfolio-builder rollback failed" in result.stdout
