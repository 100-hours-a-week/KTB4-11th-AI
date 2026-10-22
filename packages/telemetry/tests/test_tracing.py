import json
import logging
import time

import ktb_telemetry.tracing as tracing_module
import pytest
from ktb_core.logging import get_logger, setup_logging
from ktb_telemetry.tracing import tracing
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def test_tracing_names_the_service_and_flushes_on_exit(monkeypatch):
    exporter = InMemorySpanExporter()
    providers = []
    monkeypatch.setattr(tracing_module, "OTLPSpanExporter", lambda **kwargs: exporter)
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", providers.append)
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=test")

    with tracing("market-streamer"):
        providers[0].get_tracer("t").start_span("publish").end()

    (span,) = exporter.get_finished_spans()
    assert span.resource.attributes["service.name"] == "market-streamer"
    assert span.resource.attributes["deployment.environment"] == "test"


def test_tracing_disabled_creates_no_recording_spans(monkeypatch):
    providers = []
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", providers.append)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    with tracing("svc"):
        assert not providers[0].get_tracer("t").start_span("s").is_recording()


def test_logs_inside_a_span_carry_trace_ids_and_keep_fields(capsys, monkeypatch):
    providers = []
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", providers.append)
    setup_logging("INFO", service_name="svc")
    with tracing("svc"):
        with providers[0].get_tracer("t").start_as_current_span("work") as span:
            get_logger("svc.child", run_id="r1").info("structured", n=2)
            logging.getLogger("uvicorn.error").info("plain %s", "arg")
    structured, plain = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    context = span.get_span_context()
    for payload in (structured, plain):
        assert payload["trace_id"] == format(context.trace_id, "032x")
        assert payload["span_id"] == format(context.span_id, "016x")
    assert (structured["run_id"], structured["n"]) == ("r1", 2)
    assert plain["message"] == "plain arg"


def test_logs_outside_spans_and_after_exit_have_no_trace_ids(capsys, monkeypatch):
    providers = []
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", providers.append)
    setup_logging("INFO", service_name="svc")
    with tracing("svc"):
        get_logger("svc").info("outside")
        span = providers[0].get_tracer("t").start_span("held")
    with trace.use_span(span):
        get_logger("svc").info("after_exit")
    for line in capsys.readouterr().out.splitlines():
        assert "trace_id" not in json.loads(line)


def test_unreachable_exporter_neither_raises_nor_stalls(monkeypatch):
    providers = []
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", providers.append)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    started = time.monotonic()
    with tracing("svc"):
        providers[0].get_tracer("t").start_span("s").end()
    assert time.monotonic() - started < 4
    assert logging.getLogger("opentelemetry.exporter").getEffectiveLevel() == logging.ERROR


@pytest.mark.parametrize(
    "env",
    [
        {"OTEL_TRACES_SAMPLER": "bogus"},
        {"OTEL_TRACES_SAMPLER": "parentbased_traceidratio", "OTEL_TRACES_SAMPLER_ARG": "abc"},
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "not-a-url"},
        {"OTEL_RESOURCE_ATTRIBUTES": "garbage"},
    ],
)
def test_misconfiguration_never_raises(monkeypatch, env):
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", lambda provider: None)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with tracing("svc"):
        pass
