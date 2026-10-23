# Price stream tracing (#368)

`market-streamer` and `market-http` export OpenTelemetry traces to Tempo in the VPC
(PLG stack) so one price update can be followed from the Kiwoom frame to each SSE client.
Tracing is off by default: `tracing()` does nothing unless `OTEL_EXPORTER_OTLP_ENDPOINT`
(or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`) is set, and every Compose file also sets
`OTEL_SDK_DISABLED=true`.

Issue #368 named `ktb_core/telemetry.py`. Tracing lives in the `ktb_telemetry` package
(`packages/telemetry`) instead, so `ktb_core` stays free of third-party dependencies.
Only `market-streamer` and `market-http` depend on it.

## Trace shape

```text
publish            market-streamer, PRODUCER   one per publish() batch
  deliver          market-http, CONSUMER       one per SSE client that receives the event
```

- `publish` covers the read-compare-write work for one batch of prices. Attributes:
  `prices.received`, `prices.accepted`.
- When `publish` is sampled, each accepted price message carries two extra JSON fields:
  `traceparent` (W3C trace context) and `published_at` (T0, ISO 8601 UTC). Unsampled
  messages are unchanged. The SDK emits trace flags `03` (sampled plus the random-trace-id
  bit); any valid flags are accepted by market-http.
- `deliver` is a child of `publish`. Attributes: `code`, `sse.connection_id` (filter one
  client's stream), `published_at`, `publish_to_receive_ms`.
- The SSE snapshot sent when a client connects has no span. REST routes get FastAPI's
  built-in telemetry: a server span plus spans for dependency resolution, the endpoint, and
  serialization. The `/stream` routes are excluded from it (`telemetry={"exclude": ...}`) so
  no span lasts as long as a connection.

## T0, T1, T2

| Point | Meaning |
| --- | --- |
| T0 | `published_at`, taken just before the write pipeline executes |
| T1 | `deliver` span start, right after `pubsub.listen()` yields the message |
| T2 | `deliver` span end, when ASGI `send()` for that event returns |

`publish_to_receive_ms` is T1 - T0. The `deliver` duration is T2 - T1.

Caveats:

- `publish_to_receive_ms` compares timestamps from two hosts; it is only meaningful when
  their clocks are synchronized (NTP/chrony). Skew shows up directly as error, including
  negative values.
- T0 is before the pipeline executes, not the moment Valkey runs `PUBLISH`. It includes
  pipeline round-trip time.
- T2 is `send()` returning, not network delivery to the client.
- `deliver` includes time waiting in FastAPI's SSE buffers, so a slow client lengthens it.

A traced message with a malformed or missing `published_at` is still delivered; its span
lacks `published_at` and `publish_to_receive_ms`. A message whose `traceparent` is not a
valid W3C trace context is delivered with no `deliver` span. A span still queued when the
client disconnects ends at the disconnect with `sse.disconnected=true`; exclude those from
latency queries.

## Sampling

- The streamer's ratio (`parentbased_traceidratio`) decides which batches are traced.
  `market-http` follows the parent for event spans, so its own ratio does not apply to them.
- `market-http` REST requests have no remote parent (inbound trace headers are ignored), so they
  are sampled locally with its ratio.
- Defaults: 0.1 for `market-streamer`, 0.01 for `market-http`.
- TraceQL results are samples, not totals. Do not read span counts as request or event
  counts.

## Load limits

One sampled publish creates a `deliver` span for every SSE client subscribed to that code,
so `deliver` spans per second are about the sampling ratio times the total SSE events
per second. Producer sampling alone cannot bound that, so `market-http` bounds it in three
places:

- **Creation rate.** A sliding-window limiter (current second plus the previous second,
  weighted by overlap) allows at most `MARKET_HTTP_TRACE_SPANS_PER_SECOND` `deliver` spans
  per second per process (default 100; 0 disables them). Over the limit, the message is
  streamed exactly as an untraced one, so the trace keeps its `publish` span but may miss
  some `deliver` children. At 0.01 sampling the default only engages above about 10,000
  SSE events per second. 100 spans per second is enough for per-minute latency
  percentiles; check Tempo's ingest volume before raising it.
- **Unfinished spans.** FastAPI streams SSE through two one-item buffers. A slow client
  blocks `send()`, which stops its generator, so new messages wait in the Valkey pub/sub
  buffer instead of becoming spans. Each connection holds about four unfinished spans at
  most, and they all end when it disconnects. There is no global cap.
- **Export queue.** `BatchSpanProcessor` keeps finished spans in a queue bounded by
  `OTEL_BSP_MAX_QUEUE_SIZE` (SDK default 2048) and drops the oldest when Tempo is slow or
  down, so memory stays bounded. This bounds memory only; spans are still created up to
  the rate limit.

## Sampling statistics

The `active_sse_connections` EMF log line that `market-http` writes every 60 s (only when
`MARKET_HTTP_METRICS_NAMESPACE` is set) also carries two per-interval counts, published to
CloudWatch with `ActiveSSEConnections`:

| Metric | Meaning |
| --- | --- |
| `DeliverSpansRecorded` | `deliver` spans created in the interval |
| `DeliverSpansSkipped` | traced deliveries streamed without a span because the rate limit was reached |

`DeliverSpansSkipped / (DeliverSpansRecorded + DeliverSpansSkipped)` is the share of traced
deliveries the limiter dropped. Read it next to any Tempo latency query: a rising skipped
share means Tempo shows a thinner sample, not that latency changed. Not counted: messages
the streamer did not sample, messages with a malformed `traceparent`, and spans the export
queue drops when Tempo is slow. When `market-http` itself has no OTLP endpoint, it creates
no `deliver` spans and both counts stay 0.

## Comparing before and after #249

A comparison is valid only when both sides use the same sampling policy: keep the
streamer's `OTEL_TRACES_SAMPLER_ARG` and `MARKET_HTTP_TRACE_SPANS_PER_SECOND` unchanged
across the deploy, compare equal time windows with similar `ActiveSSEConnections`, and
check that the skipped share above is similar on both sides.

## Environment variables

Both services use standard `OTEL_*` names. Compose maps host-side variables to them.

| Container variable | Host-side variable (`market-streamer` / `market-http`) | Default |
| --- | --- | --- |
| `OTEL_SDK_DISABLED` | `MARKET_STREAMER_OTEL_SDK_DISABLED` / `MARKET_HTTP_OTEL_SDK_DISABLED` | `true` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `MARKET_STREAMER_OTEL_EXPORTER_OTLP_ENDPOINT` / `MARKET_HTTP_OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` |
| `OTEL_TRACES_SAMPLER_ARG` | `MARKET_STREAMER_OTEL_TRACES_SAMPLER_ARG` / `MARKET_HTTP_OTEL_TRACES_SAMPLER_ARG` | `0.1` / `0.01` |
| `OTEL_RESOURCE_ATTRIBUTES` | `MARKET_STREAMER_OTEL_RESOURCE_ATTRIBUTES` / `MARKET_HTTP_OTEL_RESOURCE_ATTRIBUTES` | `deployment.environment=local` (`compose.local.yaml`), `dev` (`compose.prod.yaml`) |
| `OTEL_TRACES_SAMPLER` | none (fixed) | `parentbased_traceidratio` |
| `OTEL_PROPAGATORS` | none (fixed, `market-http` only) | `none` |
| `OTEL_BSP_MAX_QUEUE_SIZE` | none (not wired; set on the container if needed) | `2048` (SDK) |

`market-http` also reads `MARKET_HTTP_TRACE_SPANS_PER_SECOND` (default `100`), wired in
both Compose files; see [Load limits](#load-limits).

Notes:

- The default endpoint `http://localhost:4318` is the container's own loopback. Set it to
  the collector address reachable from the container.
- `OTEL_SDK_DISABLED` disables tracing only when exactly `true`; `yes` or `1` leave it on.
- The service name is fixed in code (`market-streamer`, `market-http`) and wins over
  `OTEL_SERVICE_NAME`. `OTEL_RESOURCE_ATTRIBUTES` still merges, so
  `deployment.environment` distinguishes environments in Tempo.
- `market-http` ignores inbound trace headers because `main()` sets an empty global
  propagator in code; `OTEL_PROPAGATORS=none` is kept as belt-and-braces. Both services use
  the W3C propagator directly rather than the global one.
- FastAPI 0.142 configures OTLP export by itself when `OTEL_EXPORTER_OTLP_ENDPOINT` is set:
  it adds a second span exporter to the provider (every span reaches Tempo twice) and
  OTLP metric and log exporters aimed at the same endpoint. `market-http` passes
  `telemetry={"auto_configure": False}` to `FastAPI()` so `ktb_telemetry` stays the only
  exporter. FastAPI 0.143 makes automatic setup opt-in, so the setting stays correct after
  an upgrade. FastAPI's request metrics and logs record nothing because no meter or logger
  provider is configured.
- Production `market-http` (ECS) takes its environment from the `MARKET_HTTP_ENV_JSON`
  GitHub Actions variable. It sets no OTLP endpoint, so tracing stays off there. To enable
  it, add `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`,
  `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod`, and `OTEL_PROPAGATORS=none` to
  that variable. `market-streamer` does not run in production.

For the AWS test system, set on both `market-streamer` and `market-http`:
`OTEL_SDK_DISABLED=false`, `OTEL_EXPORTER_OTLP_ENDPOINT=<collector in that VPC>`,
`OTEL_TRACES_SAMPLER=parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG=<ratio>`, and
`OTEL_RESOURCE_ATTRIBUTES=deployment.environment=<env>`; on `market-http` also
`OTEL_PROPAGATORS=none`.

## Failure behavior

- Tempo down: spans are dropped. Publishing, streaming, and logging are unaffected. The
  exporter logs one ERROR line per failed export batch, repeating every few seconds while
  Tempo stays down. Exclude loggers under `opentelemetry` from ERROR alerts.
- Once tracing starts, every `opentelemetry` logger is raised to ERROR. Without that, a full
  export queue logs a WARNING for each dropped span, which floods Loki under SSE load.
  Configuration warnings are still logged, because the SDK emits them before that point.
- Shutdown flushes with a 2 s exporter timeout, so exit is delayed by at most about 2 s.
- Misconfigured `OTEL_*` values (bogus sampler, non-numeric sampler argument, malformed
  endpoint, garbage resource attributes) log a WARNING and fall back to defaults; nothing
  raises.

## Log correlation

Log lines written inside a span carry `trace_id` and `span_id` in their JSON fields. The
Grafana log-to-trace link is configured outside this repository.

## Overhead

`scripts/bench-publish-tracing.py MODE` times 1000 `publish()` calls (26 prices, one
batch, after 50 warmup calls) against a local Valkey, one process per mode. `sampled-in`
and `sampled-out` use `ALWAYS_ON` / `ALWAYS_OFF` with an in-memory exporter, so network
export cost is not included. The benchmark flushes the selected Valkey database, so use a
disposable one (15 above), never the dev database.

```bash
for m in off sampled-out sampled-in; do KTB_TEST_VALKEY_URL=redis://localhost:6379/15 uv run python scripts/bench-publish-tracing.py $m; done
```

Two runs per mode (mean / p95 ms per call, max RSS):

| Mode | Run 1 | Run 2 |
| --- | --- | --- |
| `off` | 5.644 / 7.885, 146.1 MB | 5.916 / 8.901, 146.3 MB |
| `sampled-out` | 5.667 / 7.448, 146.2 MB | 5.787 / 7.968, 146.2 MB |
| `sampled-in` | 6.179 / 9.086, 149.2 MB | 5.835 / 7.584, 149.4 MB |

Run-to-run variation (about 0.3 ms) is larger than the difference between modes, so
tracing overhead on `publish()` is not distinguishable from noise at this sample size.
Sampling in adds about 3 MB of peak RSS, most likely the in-memory exporter retaining the benchmark's spans.

Payload growth: a sampled price message gains `traceparent` and `published_at`, which
measured 126 bytes (121 to 247 bytes of `json.dumps` output for a representative message).

