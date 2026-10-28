import argparse
import copy
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

DEFAULTS = {
    "OTEL_SDK_DISABLED": "false",
    "OTEL_SERVICE_NAME": "market-http",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
    "OTEL_TRACES_SAMPLER_ARG": "0.1",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
    "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment=prod,deployment.environment.name=prod",
}


def render(template, image, secret, environment):
    if not isinstance(environment, dict) or not environment:
        raise ValueError("MARKET_HTTP_ENV_JSON must be a nonempty JSON object")
    if not all(
        isinstance(k, str) and k.isidentifier() and isinstance(v, str)
        for k, v in environment.items()
    ):
        raise ValueError("Environment must contain valid names and string values")
    if "MARKET_HTTP_POSTGRES_DSN" in environment:
        raise ValueError("MARKET_HTTP_POSTGRES_DSN must use the AWS secret reference")
    if not image or not secret:
        raise ValueError("Image and PostgreSQL secret ARN are required")
    env = DEFAULTS | environment
    endpoint = urlsplit(env.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""))
    if (
        endpoint.scheme != "http"
        or endpoint.port != 4318
        or not endpoint.hostname
        or not ipaddress.ip_address(endpoint.hostname).is_private
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or endpoint.path not in ("", "/")
    ):
        raise ValueError(
            "OTEL_EXPORTER_OTLP_ENDPOINT must be a private Tempo HTTP endpoint on 4318"
        )
    if "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in env:
        raise ValueError("Use OTEL_EXPORTER_OTLP_ENDPOINT for the reviewed Tempo destination")
    required = {
        k: DEFAULTS[k]
        for k in (
            "OTEL_SDK_DISABLED",
            "OTEL_SERVICE_NAME",
            "OTEL_EXPORTER_OTLP_PROTOCOL",
            "OTEL_TRACES_EXPORTER",
            "OTEL_TRACES_SAMPLER",
            "OTEL_METRICS_EXPORTER",
            "OTEL_LOGS_EXPORTER",
        )
    }
    if any(env[k] != v for k, v in required.items()):
        raise ValueError(
            "market-http must enable native OTLP HTTP tracing with metrics/log export disabled"
        )
    if not 0 < float(env["OTEL_TRACES_SAMPLER_ARG"]) <= 1:
        raise ValueError("Trace sample ratio must be greater than zero and at most one")
    task = copy.deepcopy(template)
    containers = [c for c in task["containerDefinitions"] if c["name"] == "market-http"]
    if len(containers) != 1 or containers[0].get("command") != ["market-http"]:
        raise ValueError("Expected one native market-http application container")
    container = containers[0]
    container["image"] = image
    container["environment"] = [{"name": k, "value": v} for k, v in sorted(env.items())]
    container["secrets"] = [{"name": "MARKET_HTTP_POSTGRES_DSN", "valueFrom": secret}]
    return task


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    task = render(
        json.loads(args.template.read_text()),
        args.image,
        args.secret,
        json.loads(os.environ["MARKET_HTTP_ENV_JSON"]),
    )
    args.output.write_text(json.dumps(task, indent=2) + "\n")


if __name__ == "__main__":
    main()
