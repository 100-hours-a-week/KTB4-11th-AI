import logging
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            fields = getattr(record, "fields", None)
            record.fields = {
                **(fields if isinstance(fields, dict) else {}),
                "trace_id": trace.format_trace_id(context.trace_id),
                "span_id": trace.format_span_id(context.span_id),
            }
        return True


@contextmanager
def tracing(service_name: str) -> Iterator[None]:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(timeout=2)))
    trace.set_tracer_provider(provider)
    logging.getLogger("opentelemetry.exporter").setLevel(logging.ERROR)
    handlers = list(logging.getLogger().handlers)
    trace_filter = TraceContextFilter()
    for handler in handlers:
        handler.addFilter(trace_filter)
    try:
        yield
    finally:
        for handler in handlers:
            handler.removeFilter(trace_filter)
        provider.shutdown()
