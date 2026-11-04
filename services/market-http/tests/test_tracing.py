import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
import redis
from fastapi.testclient import TestClient
from market_http.app import create_app
from market_http.observability.pool import observe_pool
from market_http.observability.prometheus import MarketMetrics
from market_http.observability.tracing import (
    DeliverSpans,
    SlidingWindowLimiter,
    SSESendSpans,
    track_event,
)
from market_http.repositories import prices as prices_module
from opentelemetry import metrics, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import SpanKind
from redis.asyncio import Redis

from .example_entities import ORDERBOOK
from .test_market_subscription import FakeValkey

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
PARENT_ID = "b7ad6b7169203331"
PRICE = {
    "code": "005930",
    "price": 82000,
    "change": 500,
    "rate": 0.61,
    "volume": 1000,
    "timestamp": "2026-10-13T09:01:00+09:00",
}


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(prices_module, "questdb_prices", lambda conf, codes: [])
    monkeypatch.setattr(
        prices_module,
        "current_day_price_limits",
        lambda conf, codes: {
            code: {"upper_limit_price": 107200, "lower_limit_price": 57700} for code in codes
        },
    )


def _payload(**extra):
    return json.dumps(PRICE | extra)


def _traced(flags="01"):
    return _payload(
        traceparent=f"00-{TRACE_ID}-{PARENT_ID}-{flags}",
        published_at=datetime.now(UTC).isoformat(),
    )


async def _drive_stream(app, query, on_body, path="/prices/stream"):
    disconnect = asyncio.Event()
    messages = []
    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request"}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            await on_body(message["body"], disconnect)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query.encode(),
        "headers": [(b"accept", b"text/event-stream")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(app(scope, receive, send), 10)
    return messages


def _stream_events(valkey_url, publish, **app_options):
    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        app = create_app(valkey, "conf", **app_options)
        bodies = []

        async def on_body(body, disconnect):
            bodies.append(body)
            if len(bodies) == 1:
                await valkey.publish("market:prices", publish)
            else:
                disconnect.set()

        await _drive_stream(app, "codes=005930", on_body)
        await valkey.aclose()
        return bodies

    return asyncio.run(scenario())


def _seed(valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", json.dumps(PRICE))


def _deliver_spans(spans):
    return [s for s in spans.get_finished_spans() if s.name == "deliver"]


def _event_data(body):
    return json.loads(body.split(b"data: ", 1)[1])


@pytest.mark.parametrize("flags", ["01", "03"])
def test_traced_message_gets_a_deliver_span_under_the_publish_span(valkey_url, spans, flags):
    _seed(valkey_url)

    bodies = _stream_events(valkey_url, _traced(flags))

    (deliver,) = _deliver_spans(spans)
    assert deliver.parent.span_id == int(PARENT_ID, 16)
    assert deliver.context.trace_id == int(TRACE_ID, 16)
    assert deliver.kind == SpanKind.CONSUMER
    assert deliver.attributes["code"] == "005930"
    assert deliver.attributes["publish_to_receive_ms"] >= 0
    assert deliver.attributes["sse.connection_id"]
    assert deliver.attributes["published_at"]
    assert b"traceparent" not in bodies[1]
    assert b"published_at" not in bodies[1]
    assert _event_data(bodies[1])["code"] == "005930"


def test_untraced_message_gets_no_span(valkey_url, spans):
    _seed(valkey_url)

    _stream_events(valkey_url, _payload())

    assert not _deliver_spans(spans)


def test_traced_message_over_the_span_limit_streams_without_a_span(valkey_url, spans):
    _seed(valkey_url)

    bodies = _stream_events(valkey_url, _traced(), trace_spans_per_second=0)

    assert _event_data(bodies[1])["price"] == 82000
    assert not _deliver_spans(spans)


def test_deliver_spans_count_recorded_and_skipped(spans):
    deliver_spans = DeliverSpans(1)

    assert deliver_spans.start(_payload(), "c") is None
    deliver_spans.start(_traced(), "c").end()
    assert deliver_spans.start(_traced(), "c") is None

    assert deliver_spans.take_counts() == (1, 1)
    assert deliver_spans.take_counts() == (0, 0)


def test_deliver_spans_are_not_created_or_counted_while_tracing_is_off(monkeypatch):
    monkeypatch.setattr(trace, "get_tracer_provider", trace.ProxyTracerProvider)
    deliver_spans = DeliverSpans(1)

    assert deliver_spans.start(_traced(), "c") is None
    assert deliver_spans.take_counts() == (0, 0)


def test_deliver_span_uses_pubsub_receipt_time_before_queue_wait(spans):
    published = datetime(2026, 10, 13, tzinfo=UTC)
    received = published + timedelta(milliseconds=125)
    raw = _payload(traceparent=f"00-{TRACE_ID}-{PARENT_ID}-01", published_at=published.isoformat())

    async def app(scope, receive, send):
        track_event(raw, received=received)
        await send({"type": "http.response.body", "body": b"data: live\n\n"})

    async def send(message):
        pass

    asyncio.run(_run(SSESendSpans(app, DeliverSpans(100)), send))
    (deliver,) = _deliver_spans(spans)
    assert deliver.attributes["publish_to_receive_ms"] == 125.0


def test_track_event_outside_a_stream_does_nothing():
    track_event("anything")


def test_sliding_window_limiter_weights_the_previous_second():
    limiter = SlidingWindowLimiter(4)

    assert [limiter.allow(10.0) for _ in range(5)] == [True, True, True, True, False]
    assert [limiter.allow(11.5) for _ in range(3)] == [True, True, False]
    assert [limiter.allow(12.25) for _ in range(5)] == [True, True, True, False, False]
    assert [limiter.allow(20.0) for _ in range(5)] == [True, True, True, True, False]


def test_malformed_traceparent_still_streams_without_a_span(valkey_url, spans):
    _seed(valkey_url)

    bodies = _stream_events(
        valkey_url, _payload(traceparent="garbage", published_at=datetime.now(UTC).isoformat())
    )

    assert _event_data(bodies[1])["price"] == 82000
    assert not _deliver_spans(spans)


@pytest.mark.parametrize("published_at", ["not-a-date", 12345, None, "2026-10-13T09:01:00"])
def test_malformed_published_at_still_streams(valkey_url, spans, published_at):
    _seed(valkey_url)

    bodies = _stream_events(
        valkey_url,
        _payload(traceparent=f"00-{TRACE_ID}-{PARENT_ID}-01", published_at=published_at),
    )

    assert _event_data(bodies[1])["price"] == 82000
    (deliver,) = _deliver_spans(spans)
    assert "publish_to_receive_ms" not in deliver.attributes
    assert "published_at" not in deliver.attributes


@pytest.mark.parametrize("traceparent", [123, None, ["x"]])
def test_non_string_traceparent_still_streams_without_a_span(valkey_url, spans, traceparent):
    _seed(valkey_url)

    bodies = _stream_events(valkey_url, _payload(traceparent=traceparent))

    assert _event_data(bodies[1])["price"] == 82000
    assert not _deliver_spans(spans)


class _NamedSpans:
    def start(self, raw, connection_id, *, received=None):
        return trace.get_tracer("test").start_span(raw)


def _span_app(sends):
    async def app(scope, receive, send):
        track_event("a")
        track_event("b")
        for body in sends:
            await send({"type": "http.response.body", "body": body})

    return SSESendSpans(app, _NamedSpans())


async def _run(app, send):
    await app({"type": "http", "path": "/prices/stream"}, None, send)


def test_keepalive_does_not_end_an_event_span(spans):
    ended_after_send = []

    async def send(message):
        ended_after_send.append([s.name for s in spans.get_finished_spans()])

    asyncio.run(_run(_span_app([b": ping\n\n", b"event: price\n\n", b"event: price\n\n"]), send))

    assert ended_after_send == [[], [], ["a"]]
    assert [s.name for s in spans.get_finished_spans()] == ["a", "b"]


def test_disconnect_ends_queued_spans(spans):
    async def send(message):
        pass

    asyncio.run(_run(_span_app([]), send))

    assert [s.name for s in spans.get_finished_spans()] == ["a", "b"]
    assert all(s.attributes["sse.disconnected"] for s in spans.get_finished_spans())


def test_rest_requests_get_server_spans_and_stream_routes_do_not(valkey_url, spans):
    _seed(valkey_url)
    app = create_app(Redis.from_url(valkey_url, decode_responses=True), "conf")

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    async def disconnect_first(body, disconnect):
        disconnect.set()

    asyncio.run(_drive_stream(app, "codes=005930", disconnect_first))

    servers = [s for s in spans.get_finished_spans() if s.kind == SpanKind.SERVER]
    assert any("/health" in s.name for s in servers)
    assert not any("stream" in s.name for s in servers)


def test_fastapi_does_not_add_its_own_exporters(monkeypatch, spans):
    added = []
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setattr(TracerProvider, "add_span_processor", added.append)
    monkeypatch.setattr(metrics, "set_meter_provider", added.append)

    with TestClient(create_app(FakeValkey(), "unused")):
        pass

    assert added == []


def test_rest_prices_ignore_trace_fields(valkey_url):
    redis.Redis.from_url(valkey_url).set("price:005930", _traced())
    app = create_app(Redis.from_url(valkey_url, decode_responses=True), "conf")

    with TestClient(app) as client:
        body = client.get("/prices?codes=005930").json()

    assert body[0]["code"] == "005930"
    assert "traceparent" not in body[0]
    assert "published_at" not in body[0]


@pytest.mark.parametrize(
    "stream,key_prefix,publish_channel",
    [("prices", "price", "market:prices"), ("orderbooks", "orderbook", "market:orderbooks")],
)
def test_prometheus_counts_snapshot_and_live_event_independent_of_traces(
    valkey_url, stream, key_prefix, publish_channel
):
    metrics = MarketMetrics()
    payload = _payload() if stream == "prices" else ORDERBOOK.model_dump_json()

    async def scenario():
        valkey = Redis.from_url(valkey_url, decode_responses=True)
        observe_pool(valkey.connection_pool, metrics)
        try:
            await valkey.set(f"{key_prefix}:005930", payload)
            app = create_app(valkey, "conf", prometheus=metrics, trace_spans_per_second=0)
            bodies = []

            async def on_body(body, disconnect):
                bodies.append(body)
                if len(bodies) == 1:
                    await valkey.publish(publish_channel, payload)
                else:
                    disconnect.set()

            await _drive_stream(app, "codes=005930", on_body, f"/{stream}/stream")
            return bodies
        finally:
            await valkey.aclose()

    bodies = asyncio.run(scenario())
    assert (
        metrics.registry.get_sample_value("market_http_sse_events_sent_total", {"stream": stream})
        == 2
    )
    assert metrics.registry.get_sample_value(
        "market_http_sse_events_sent_bytes_total", {"stream": stream}
    ) == sum(len(body) for body in bodies)
    assert (
        metrics.registry.get_sample_value(
            "market_http_sse_delivery_latency_seconds_count", {"stream": stream}
        )
        == 1
    )
    assert (
        metrics.registry.get_sample_value(
            "market_http_sse_connections_closed_total",
            {"stream": stream, "reason": "client_disconnect"},
        )
        == 1
    )
    assert metrics.registry.get_sample_value("market_http_sse_connections", {"stream": stream}) == 0
    assert (
        metrics.registry.get_sample_value(
            "market_http_valkey_pool_acquire_duration_seconds_count", {"result": "success"}
        )
        > 0
    )
