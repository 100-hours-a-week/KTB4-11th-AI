import argparse
import importlib.util
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("ssm_env", ROOT / "scripts/ssm_env.py")
ssm_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ssm_env)


@pytest.fixture
def args(tmp_path):
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n")
    base = tmp_path / "base.env"
    base.write_text("LOG_LEVEL=INFO\n")
    return argparse.Namespace(
        environment="dev",
        region="ap-northeast-2",
        aws_cli="aws",
        output=tmp_path / ".env.next",
        compose_file=compose,
        base_env=base,
    )


def test_merges_only_explicit_references_in_batches(monkeypatch, args):
    values = {f"KEY_{i}": f"ssm:///stockspoon/ai/dev/PARAM_{i}" for i in range(12)}
    values.update(LOG_LEVEL="DEBUG", ALIAS="ssm:///stockspoon/ai/dev/PARAM_0")
    monkeypatch.setattr(ssm_env, "read_base_env", lambda path: values.copy())
    calls = []

    def request(arguments, operation, payload):
        calls.append(payload)
        assert operation == "get-parameters"
        assert payload["WithDecryption"] is True
        return {
            "Parameters": [
                {"Name": name, "Value": "private-value", "Type": "SecureString"}
                for name in reversed(payload["Names"])
            ]
        }

    monkeypatch.setattr(ssm_env, "aws_ssm", request)
    ssm_env.fetch(args)
    assert [len(call["Names"]) for call in calls] == [10, 2]
    assert stat.S_IMODE(args.output.stat().st_mode) == 0o600
    content = args.output.read_text()
    assert 'LOG_LEVEL="DEBUG"' in content
    assert 'ALIAS="private-value"' in content
    assert not list(args.output.parent.glob(".ssm-env-*"))


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"POSTGRES_PASSWORD": "plaintext"},
        {"POSTGRES_PASSWORD": None},
        {"PASSWORD": "ssm:///stockspoon/ai/prod/PASSWORD"},
        {"PASSWORD": "ssm:///stockspoon/ai/dev/nested/PASSWORD"},
        {"PASSWORD": "ssm://missing-leading-slash"},
        {"APP_IMAGE": "image"},
        {"BACKEND_URL": "https://user:password@host"},
    ],
)
def test_invalid_base_preserves_current_file_without_aws_calls(monkeypatch, args, values):
    monkeypatch.setattr(ssm_env, "read_base_env", lambda path: values)

    def forbidden(*unused):
        pytest.fail("Must validate configuration before querying secrets")

    monkeypatch.setattr(ssm_env, "aws_ssm", forbidden)
    args.output.write_text("previous release")
    with pytest.raises(ValueError):
        ssm_env.fetch(args)
    assert args.output.read_text() == "previous release"


@pytest.mark.parametrize(
    "response",
    [
        {"InvalidParameters": ["/stockspoon/ai/dev/PASSWORD"]},
        {"Parameters": []},
        {"Parameters": [{"Name": "/stockspoon/ai/dev/PASSWORD", "Value": "x", "Type": "String"}]},
        {
            "Parameters": [
                {"Name": "/stockspoon/ai/prod/PASSWORD", "Value": "x", "Type": "SecureString"}
            ]
        },
    ],
)
def test_invalid_ssm_response_preserves_current_file(monkeypatch, args, response):
    monkeypatch.setattr(
        ssm_env, "read_base_env", lambda path: {"PASSWORD": "ssm:///stockspoon/ai/dev/PASSWORD"}
    )
    monkeypatch.setattr(ssm_env, "aws_ssm", lambda *unused: response)
    args.output.write_text("previous release")
    with pytest.raises(ValueError):
        ssm_env.fetch(args)
    assert args.output.read_text() == "previous release"


def test_missing_required_setting_fails_before_replacing_file(monkeypatch, args):
    monkeypatch.setattr(ssm_env, "read_base_env", lambda path: {"LOG_LEVEL": "INFO"})
    args.compose_file.write_text("image: ${APP_IMAGE:?required}\nvalue: ${POSTGRES_USER:?required}")
    args.output.write_text("previous release")
    with pytest.raises(ValueError, match="POSTGRES_USER"):
        ssm_env.fetch(args)
    assert args.output.read_text() == "previous release"


def test_upload_prepares_one_variable_and_uploads_only_secrets(monkeypatch, args, capsys):
    pytest.importorskip("dotenv")
    args.env_file = args.output.parent / "source.env"
    args.env_file.write_text("LOG_LEVEL=INFO\nAPI_KEY='secret$literal'\nEMPTY=\nAPP_IMAGE=old\n")
    args.base_output = args.output.parent / "github.env"
    args.secure_key = []
    args.apply = False
    args.overwrite = False
    calls = []
    monkeypatch.setattr(ssm_env, "aws_ssm", lambda *call: calls.append(call))
    ssm_env.upload(args)
    assert "secret$literal" not in capsys.readouterr().out
    assert "secret$literal" not in args.base_output.read_text()
    assert 'API_KEY="ssm:///stockspoon/ai/dev/API_KEY"' in args.base_output.read_text()
    assert 'LOG_LEVEL="INFO"' in args.base_output.read_text()
    assert not calls
    args.apply = True
    ssm_env.upload(args)
    assert len(calls) == 1
    assert calls[0][2]["Value"] == "secret$literal"
    assert calls[0][2]["Type"] == "SecureString"
    assert calls[0][2]["Overwrite"] is False


@pytest.mark.parametrize("failure", [False, True])
def test_aws_payload_uses_private_temporary_file_and_removes_it(monkeypatch, args, failure):
    requests = []

    def run(command, **kwargs):
        request = Path(command[command.index("--cli-input-json") + 1].removeprefix("file://"))
        requests.append(request)
        assert stat.S_IMODE(request.stat().st_mode) == 0o600
        assert json.loads(request.read_text())["Value"] == "private-secret"
        assert "private-secret" not in " ".join(command)
        assert "input" not in kwargs
        if failure:
            return subprocess.CompletedProcess(
                command,
                254,
                "",
                "An error occurred (AccessDeniedException): rejected private-secret",
            )
        return subprocess.CompletedProcess(command, 0, '{"Version": 1}', "")

    monkeypatch.setattr(ssm_env.subprocess, "run", run)
    if failure:
        with pytest.raises(ssm_env.ConfigurationError) as error:
            ssm_env.aws_ssm(args, "put-parameter", {"Value": "private-secret"})
        assert "AccessDeniedException" in str(error.value)
        assert "private-secret" not in str(error.value)
    else:
        assert ssm_env.aws_ssm(args, "put-parameter", {"Value": "private-secret"}) == {"Version": 1}
    assert len(requests) == 1
    assert not requests[0].exists()


@pytest.mark.skipif(shutil.which("aws") is None, reason="AWS CLI unavailable")
@pytest.mark.parametrize(
    "operation,payload",
    [
        (
            "put-parameter",
            {
                "Name": "/stockspoon/ai/dev/EXAMPLE",
                "Type": "SecureString",
                "Value": "dummy-test-value",
                "Overwrite": True,
            },
        ),
        ("get-parameters", {"Names": ["/stockspoon/ai/dev/EXAMPLE"], "WithDecryption": True}),
    ],
)
def test_real_aws_cli_accepts_request_without_sending_it(monkeypatch, args, operation, payload):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    run = subprocess.run

    def generate_skeleton(command, **kwargs):
        return run(command + ["--generate-cli-skeleton", "output"], **kwargs)

    monkeypatch.setattr(ssm_env.subprocess, "run", generate_skeleton)
    assert isinstance(ssm_env.aws_ssm(args, operation, payload), dict)


def test_main_reports_safe_error_details(monkeypatch, args, capsys):
    def fail(unused):
        raise ssm_env.ConfigurationError("SSM put-parameter failed (AccessDeniedException)")

    monkeypatch.setattr(ssm_env, "upload", fail)
    monkeypatch.setattr(
        "sys.argv",
        [
            "ssm_env.py",
            "--environment",
            "prod",
            "upload",
            "--env-file",
            str(args.base_env),
        ],
    )
    assert ssm_env.main() == 1
    assert "AccessDeniedException" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose unavailable")
def test_full_env_file_is_parsed_and_secret_characters_survive(monkeypatch, args):
    args.base_env.write_text(
        '# comment\nLOG_LEVEL=INFO\nJSON=\'["one","two"]\'\n'
        "PASSWORD=ssm:///stockspoon/ai/dev/PASSWORD\n"
    )
    secret = "literal$HOME${UNSET}$$\"quote'\\slash\\\nline2\r\t한글"
    monkeypatch.setattr(
        ssm_env,
        "aws_ssm",
        lambda *unused: {
            "Parameters": [
                {"Name": "/stockspoon/ai/dev/PASSWORD", "Value": secret, "Type": "SecureString"}
            ]
        },
    )
    ssm_env.fetch(args)
    values = ssm_env.read_base_env(args.output)
    assert values == {"LOG_LEVEL": "INFO", "JSON": '["one","two"]', "PASSWORD": secret}
    config = {
        "services": {"test": {"image": "busybox", "environment": {"PASSWORD": "${PASSWORD}"}}}
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(args.output),
            "-f",
            "-",
            "config",
            "--format",
            "json",
        ],
        input=json.dumps(config),
        text=True,
        capture_output=True,
        env={key: value for key, value in os.environ.items() if key != "PASSWORD"},
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["services"]["test"]["environment"][
        "PASSWORD"
    ] == secret.replace("$", "$$")
