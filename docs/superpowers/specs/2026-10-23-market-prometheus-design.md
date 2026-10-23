# Market Prometheus instrumentation

Date: 2026-10-23
Status: Approved with explicit environment-variable binding configuration
Issue: https://github.com/100-hours-a-week/KTB4-11th-AI/issues/392

## Purpose and scope

Expose the issue's twelve Prometheus metric families for market load tests while
preserving CloudWatch EMF, OpenTelemetry, existing SSE payloads, and Valkey pool
failure behavior. Do not implement shared subscriptions, change trading behavior,
or configure external Grafana/Prometheus infrastructure.

## Approaches

Recommended: use the official prometheus-client package and a separate internal
HTTP listener for each process. Both serve `/metrics` on port 9100 inside their
own container. Bind to loopback by default both directly and in Compose. No metrics ports are
published by default. Operators can explicitly configure a private-network
listener or a loopback-only host mapping. Remote production scraping
requires an explicitly configured private interface and network rules restricted
to Prometheus. Do not route metrics through the public API load balancer.

Alternative: mount metrics on the public FastAPI application and use a separate
listener only for the streamer. This needs route-specific perimeter restrictions
and makes accidental public exposure easier. Pushgateway adds another lifecycle
and stale-series management requirement for the streamer's intraday process.

The user requested MARKET_HTTP_PROMETHEUS_BIND_HOST and MARKET_STREAMER_PROMETHEUS_BIND_HOST, both defaulting to 127.0.0.1. Use PROMETHEUS_PORT under the same service prefixes, default 9100. Compose must preserve the requested loopback default; container-network scraping requires an explicit bind-host override. No public port mapping is added by default.

## Collection and lifecycle

Each process owns its registry. Listener startup failures fail startup visibly;
shutdown closes the listener and joins its thread. Configure validated host and
port settings per service, document them in `.env.example`, and wire both Compose
files. Use one process per scrape target; document that replicas are separate
targets and counters reset on restart.

Keep collection hooks and all Prometheus imports in each service's observability
package. Declare prometheus-client in both service manifests; update uv.lock and
the two service requirement exports plus docker/requirements/app.txt.

## SSE observations

An ASGI observer counts a connection only after a successful SSE response start.
Validation errors are not accepted connections. Always release the active gauge
once in finally. Track disconnect messages, cancellation, send failures, and
lifespan shutdown to classify termination; use unknown for other failures.

Count complete data events and their UTF-8 wire bytes only after ASGI send returns.
Exclude keepalive comments and empty terminal bodies. Preserve framing across body
chunks rather than assuming a body always contains exactly one event. Track each
Pub/Sub receipt with a monotonic timestamp, correlate it to the yielded event,
and observe latency after its completed send. Initial snapshots contribute to
event and byte counters but have no Pub/Sub delivery latency. Keep this tracking
independent of trace sampling and traceparent availability.

## Streamer observations

Count each original 0B or 0D data entry before validation or deduplication, including
entries later rejected. Since the SDK supplies decoded dictionaries, byte counts
measure each original entry re-encoded as compact UTF-8 JSON, not exact socket
frame bytes, transport overhead, or normalized output. State this explicitly in
metric help and operations documentation. Count all entries in a received frame
before awaiting publication. The last receipt time is recorded with a monotonic
clock; a scrape callback calculates its age so silence increases the gauge.
Before the first receipt, expose NaN rather than implying fresh data.

## Valkey pool

Instrument the existing nonblocking ConnectionPool acquisition method without
introducing a semaphore, retry, blocking pool, or timeout. Measure every completed
acquisition as success, actual timeout, or error. Pool exhaustion is an error,
not a timeout. Propagate exceptions and cancellation unchanged.

Expose actual in-use and available connection counts. The acquiring state measures
in-flight acquisition calls until they complete (including connection setup),
not an internal queue or a count of threads waiting for a free connection.
Explain this qualification in HELP text and the operations document. Snapshot
mutable state safely for the metrics listener thread.

## Metric contract

| Name | Type | Unit | Labels |
| --- | --- | --- | --- |
| market_http_sse_connections | Gauge | connections | stream |
| market_http_sse_connections_started_total | Counter | connections | stream |
| market_http_sse_connections_closed_total | Counter | connections | stream, reason |
| market_streamer_market_events_received_total | Counter | events | kind |
| market_streamer_market_events_received_bytes_total | Counter | bytes | kind |
| market_http_sse_events_sent_total | Counter | events | stream |
| market_http_sse_events_sent_bytes_total | Counter | bytes | stream |
| market_streamer_market_data_age_seconds | Gauge | seconds | kind |
| market_http_sse_delivery_latency_seconds | Histogram | seconds | stream |
| market_http_valkey_pool_connections | Gauge | connections | state |
| market_http_valkey_pool_acquire_duration_seconds | Histogram | seconds | result |
| market_http_valkey_pool_acquire_timeouts_total | Counter | timeouts | none |

Allowed values: stream = prices/orderbooks; kind = price/orderbook;
reason = normal/client_disconnect/cancelled/send_error/server_shutdown/unknown;
state = in_use/available/acquiring; result = success/timeout/error.
No user, symbol, connection, or trace identifiers become labels. Initialize
bounded label combinations so idle services remain discoverable.

## Verification and handoff

Add focused tests for metric names/types/labels, idle scrapes, connection outcomes,
successful versus failed sends, initial versus live events, split SSE bodies,
silence age growth, malformed and duplicate raw events, pool saturation and actual
timeouts, cancellation cleanup, and listener shutdown. Preserve existing EMF and
tracing regression coverage. Use disposable datastores only.

Run both service suites, Ruff lint/format, Tach boundaries and external dependency
checks. Verify exports and isolated service imports; build affected images when
Docker is available. Report unavailable integration resources explicitly.

Provide an operations document with scrape targets, private access configuration,
the exact metric contract and qualifications, rate queries, latency quantiles,
age/no-data alerts, and verification commands. Open the implementation PR against
dev with `Closes #392`. Production deployment, live load tests, and Cloud-team
infrastructure changes remain separate from this repository implementation.
