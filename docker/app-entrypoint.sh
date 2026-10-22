#!/bin/sh
set -eu

if [ "${1:-}" = "market-http" ] \
    && [ -n "${OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:-${OTEL_EXPORTER_OTLP_ENDPOINT:-}}" ] \
    && [ "${OTEL_TRACES_EXPORTER:-otlp}" != "none" ]; then
    export OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-market-http}"
    export OTEL_EXPORTER_OTLP_PROTOCOL="${OTEL_EXPORTER_OTLP_PROTOCOL:-http/protobuf}"
    export OTEL_TRACES_EXPORTER="${OTEL_TRACES_EXPORTER:-otlp}"
    export OTEL_METRICS_EXPORTER="${OTEL_METRICS_EXPORTER:-none}"
    export OTEL_LOGS_EXPORTER="${OTEL_LOGS_EXPORTER:-none}"
    export OTEL_TRACES_SAMPLER="${OTEL_TRACES_SAMPLER:-parentbased_traceidratio}"
    export OTEL_TRACES_SAMPLER_ARG="${OTEL_TRACES_SAMPLER_ARG:-0.1}"
    export OTEL_PYTHON_LOG_CORRELATION="${OTEL_PYTHON_LOG_CORRELATION:-true}"
    export OTEL_PYTHON_LOG_AUTO_INSTRUMENTATION=false
    export OTEL_PYTHON_FASTAPI_EXCLUDED_URLS="${OTEL_PYTHON_FASTAPI_EXCLUDED_URLS:-/health$}"
    exec opentelemetry-instrument "$@"
fi

exec "$@"
