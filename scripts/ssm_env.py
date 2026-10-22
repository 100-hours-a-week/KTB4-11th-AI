import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*\Z")
SECRET_PATTERN = re.compile(r"(?:^|_)(?:KEY|SECRET|PASSWORD|TOKEN|DSN|CREDENTIALS?)(?:_|$)")
RESERVED_KEYS = {"APP_IMAGE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}


def parameter_type(key, value, secure_keys=()):
    if key in secure_keys or SECRET_PATTERN.search(key):
        return "SecureString"
    try:
        if urlsplit(value).password is not None:
            return "SecureString"
    except ValueError:
        return "SecureString"
    return "String"


def aws_ssm(args, operation, payload):
    result = subprocess.run(
        [
            args.aws_cli,
            "ssm",
            operation,
            "--region",
            args.region,
            "--cli-input-json",
            "file:///dev/stdin",
            "--output",
            "json",
            "--no-cli-pager",
            "--no-paginate",
            "--cli-connect-timeout",
            "10",
            "--cli-read-timeout",
            "30",
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "AWS_CLI_AUTO_PROMPT": "off"},
    )
    if result.returncode:
        raise ValueError(f"SSM {operation} failed; check AWS identity, region, and IAM permissions")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ValueError(f"SSM {operation} returned invalid JSON") from None


def validate_key(key):
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("Invalid environment variable name in configuration")
    if key in RESERVED_KEYS:
        raise ValueError(f"{key} must not be stored in application parameters")


def read_base_env(path):
    configuration = {
        "services": {"config": {"image": "busybox", "env_file": [str(path.resolve())]}}
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "ssm-config",
            "--env-file",
            "/dev/null",
            "-f",
            "-",
            "config",
            "--format",
            "json",
        ],
        input=json.dumps(configuration),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ValueError("Unable to parse the GitHub Variable as a Compose env file")
    values = json.loads(result.stdout)["services"]["config"]["environment"]
    return {
        key: value.replace("$$", "$") if value is not None else None
        for key, value in values.items()
    }


def write_env(path, values):
    content = "".join(
        f"{key}={json.dumps(value, ensure_ascii=False).replace('$', '$$')}\n"
        for key, value in sorted(values.items())
    )
    descriptor, temporary = tempfile.mkstemp(prefix=".ssm-env-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def fetch(args):
    prefix = f"/stockspoon/ai/{args.environment}/"
    parameters = read_base_env(args.base_env)
    if not parameters:
        raise ValueError("The GitHub Variable must contain an env file")
    references = {}
    for key, value in parameters.items():
        validate_key(key)
        if value is None:
            raise ValueError(f"{key} must have an explicit value")
        if value.startswith("ssm://"):
            name = value[len("ssm://") :]
            if not name.startswith(prefix) or not re.fullmatch(
                r"[A-Za-z0-9_.-]+", name[len(prefix) :]
            ):
                raise ValueError(f"{key} must reference a parameter in its own environment")
            references[key] = name
        elif value and parameter_type(key, value) == "SecureString":
            raise ValueError(f"{key} must contain an ssm:// reference instead of a secret value")
    names = sorted(set(references.values()))
    secrets = {}
    for offset in range(0, len(names), 10):
        batch = names[offset : offset + 10]
        response = aws_ssm(args, "get-parameters", {"Names": batch, "WithDecryption": True})
        if response.get("InvalidParameters"):
            raise ValueError("Referenced SSM parameters do not exist")
        for parameter in response.get("Parameters", []):
            name = parameter["Name"]
            if name not in batch or name in secrets:
                raise ValueError("SSM returned an unexpected or duplicate parameter")
            if parameter["Type"] != "SecureString":
                raise ValueError("Referenced SSM parameters must use SecureString")
            secrets[name] = parameter["Value"]
        if any(name not in secrets for name in batch):
            raise ValueError("SSM did not return all referenced parameters")
    for key, name in references.items():
        parameters[key] = secrets[name]
    compose = args.compose_file.read_text()
    required = set(re.findall(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*):\?", compose)) - {"APP_IMAGE"}
    missing = sorted(key for key in required if not parameters.get(key))
    if missing:
        raise ValueError("Missing required parameters: " + ", ".join(missing))
    write_env(args.output, parameters)
    print(f"Merged {len(parameters)} settings with {len(secrets)} SSM secrets")


def upload(args):
    try:
        from dotenv import dotenv_values
    except ImportError:
        raise ValueError(
            "Run upload with: uv run --with python-dotenv python scripts/ssm_env.py"
        ) from None
    values = dotenv_values(args.env_file, interpolate=False)
    pending = []
    base_values = {}
    for key, value in sorted(values.items()):
        if key == "APP_IMAGE":
            continue
        validate_key(key)
        if value is None or value == "":
            base_values[key] = ""
            continue
        if "${" in value:
            raise ValueError(
                f"{key} contains an unresolved interpolation; use the final literal value"
            )
        if parameter_type(key, value, args.secure_key) == "String":
            base_values[key] = value
            continue
        base_values[key] = f"ssm:///stockspoon/ai/{args.environment}/{key}"
        if len(value.encode()) > 4096:
            raise ValueError(f"{key} exceeds the Standard parameter 4 KiB limit")
        pending.append(
            {
                "Name": f"/stockspoon/ai/{args.environment}/{key}",
                "Value": value,
                "Type": "SecureString",
                "Tier": "Standard",
                "Overwrite": args.overwrite,
            }
        )
    if not base_values:
        raise ValueError("No settings to prepare")
    if args.base_output is not None:
        if args.base_output.resolve() == args.env_file.resolve():
            raise ValueError("The base output must not overwrite the source env file")
        write_env(args.base_output, base_values)
        print("Prepared GitHub Variable env file with SSM references")
    for parameter in pending:
        if args.apply:
            aws_ssm(args, "put-parameter", parameter)
        print(f"{'Put' if args.apply else 'Plan'} {parameter['Type']} {parameter['Name']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True, choices=["dev", "prod"])
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--aws-cli", default=os.environ.get("AWS_CLI", "aws"))
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("--base-env", type=Path, required=True)
    fetch_parser.add_argument("--output", type=Path, required=True)
    fetch_parser.add_argument("--compose-file", type=Path, required=True)
    upload_parser = commands.add_parser("upload")
    upload_parser.add_argument("--env-file", type=Path, required=True)
    upload_parser.add_argument("--base-output", type=Path)
    upload_parser.add_argument("--secure-key", action="append", default=[])
    upload_parser.add_argument("--apply", action="store_true")
    upload_parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "fetch":
            fetch(args)
        else:
            upload(args)
    except (ValueError, OSError, KeyError, TypeError):
        print(
            "SSM configuration failed; check parameter names, types, required keys and IAM access",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
