# Market Prometheus metrics

Issue: [#392](https://github.com/100-hours-a-week/KTB4-11th-AI/issues/392).

## Collection endpoints and access

Both console entry points start a dedicated Prometheus HTTP listener for the
lifetime of their process. `/metrics` is on this listener, separate from the
public market API. The API port does not expose `/metrics`. A binding failure
fails service startup; normal or exceptional exit closes the listener.

| Service | Bind environment variable | Default | Port environment variable | Default |
| --- | --- | --- | --- | --- |
| market-http | MARKET_HTTP_PROMETHEUS_BIND_HOST | 127.0.0.1 | MARKET_HTTP_PROMETHEUS_PORT | 9100 |
| market-streamer | MARKET_STREAMER_PROMETHEUS_BIND_HOST | 127.0.0.1 | MARKET_STREAMER_PROMETHEUS_PORT | 9100 |

Ports must be 1–65535, bind hosts must be nonempty. Directly running both processes
on one machine requires distinct ports, for example HTTP 9101 and streamer 9102.
Each container has its own network namespace, so both can use 9100. The supplied
Compose files pass through these variables and preserve the loopback default;
they do not publish a metrics port. Loopback means the container/task loopback,
not the Docker host. A colocated collector in the same network namespace can
scrape the default listener.

For a separate Prometheus container, explicitly set each bind host to `0.0.0.0`
and scrape `market-http:9100` and `market-streamer:9100` on a private container
network. Do not publish those container ports on a public host interface. For
host-based collection, add loopback-only mappings (HTTP `127.0.0.1:9101:9100`,
streamer `127.0.0.1:9102:9100`) in an operator-owned Compose override and still
bind to `0.0.0.0` inside the container. For VPC collection use a private interface
and security group/firewall rules permitting only the collector. These endpoints
have no application authentication; the binding and network rules enforce access.

Production market-http runs in ECS with `awsvpc`. Cloud owns the task security
group and collector configuration. `MARKET_HTTP_ENV_JSON` can pass the two
environment variables through the existing CD workflow. A collector sidecar in
the same task can use loopback; a remote collector requires the explicit bind
override and permitted task-port access. Do not add this port to the public ALB.
No deployment or Cloud infrastructure changes are performed by this PR.

Example private-network scrape configuration, after the explicit bind overrides:

```yaml
scrape_configs:
  - job_name: market-http
    metrics_path: /metrics
    static_configs:
      - targets: ['market-http:9100']
  - job_name: market-streamer
    metrics_path: /metrics
    static_configs:
      - targets: ['market-streamer:9100']
```

Scrape every replica separately. Registries are per process and counters reset on
restart; use `rate()` before summing replicas. The streamer exits at its configured
stop time and exposes no endpoint while stopped. Schedule availability and feed
freshness alerts for expected operating/trading hours. Do not start multiple API
workers sharing one listener port; this entry point runs one Uvicorn process.

## Metric contract

| Metric | Type | Unit | Labels | Meaning |
| --- | --- | --- | --- | --- |
| market_http_sse_connections | Gauge | connections | stream | Accepted active connections |
| market_http_sse_connections_started_total | Counter | connections | stream | Successful SSE response starts |
| market_http_sse_connections_closed_total | Counter | connections | stream, reason | Closed accepted connections |
| market_streamer_market_events_received_total | Counter | events | kind | Original entries before validation/deduplication |
| market_streamer_market_events_received_bytes_total | Counter | bytes | kind | Original entries re-encoded as compact UTF-8 JSON |
| market_http_sse_events_sent_total | Counter | events | stream | Complete data events whose ASGI send returned |
| market_http_sse_events_sent_bytes_total | Counter | bytes | stream | Wire bytes for those complete SSE data events |
| market_streamer_market_data_age_seconds | Gauge | seconds | kind | Elapsed monotonic time since original receipt |
| market_http_sse_delivery_latency_seconds | Histogram | seconds | stream | Pub/Sub receipt to completed ASGI send |
| market_http_valkey_pool_connections | Gauge | connections | state | Pool counts and in-flight acquisition requests |
| market_http_valkey_pool_acquire_duration_seconds | Histogram | seconds | result | Completed pool acquisition duration |
| market_http_valkey_pool_acquire_timeouts_total | Counter | timeouts | none | Actual acquisition timeouts |

Allowed values, and no others:

- `stream`: `prices`, `orderbooks`.
- `kind`: `price`, `orderbook`.
- `reason`: `normal`, `client_disconnect`, `cancelled`, `send_error`, `server_shutdown`, `unknown`.
- `state`: `in_use`, `available`, `acquiring`.
- `result`: `success`, `timeout`, `error`.

No user, symbol, connection, trace ID, or exception text is a label. Histogram
exposition adds the standard `le` bucket label; the client also emits standard
`_created`, `_count`, `_sum`, and `_bucket` series. Histograms use the client
library's default second-based buckets (5 ms through 10 s, plus infinity).

### Measurement boundaries

SSE connections begin only after the accepted 200 response start has been sent;
validation failures do not count. Every accepted connection closes once, including
generator failures. `normal` means the response completed, `client_disconnect`
means ASGI received a disconnect, `cancelled` means task cancellation without a
disconnect, `send_error` means the ASGI send failed, and `unknown` covers other
failures. An observed Uvicorn shutdown signal takes precedence as
`server_shutdown`; a forced process kill cannot run cleanup or increment counters.

Event counts and bytes exclude keepalive comments and empty terminal bodies.
Complete data events are counted after the send containing their terminating
blank line returns, including events split across body chunks. Bytes include SSE
fields and delimiters, excluding HTTP/TCP/TLS overhead. ASGI success means the
server accepted the write, not that the client read or rendered it. Initial
snapshots contribute to events and bytes but not delivery latency. Live latency
includes parsing, encoding and backpressure after Pub/Sub receipt, independently
of OpenTelemetry sampling; it excludes upstream socket and Valkey transit time.

The Kiwoom SDK exposes decoded entries, not original socket-frame bytes. Received
bytes are therefore a reproducible payload-size measurement: each original
`0B`/`0D` entry serialized with compact separators and UTF-8 non-ASCII text.
This includes malformed entries and duplicates but excludes outer frame metadata,
JSON whitespace differences, socket framing, and transport overhead. It must not
be presented as measured network bandwidth. All entries in a frame are recorded
before awaiting publication. Feed age is calculated during each scrape and keeps
growing during silence; it is NaN before the first event of that kind.

The existing nonblocking Redis pool, retries, TLS and IAM configuration are
preserved. `in_use` and `available` read current pool counts (available means
created idle connections, not unused capacity). `acquiring` counts in-flight
`get_connection()` calls, including lock and connection setup time. It is not an
internal queue waiting for free capacity, and can overlap `in_use` during setup.
Pool exhaustion remains immediate and is `error`; built-in or Redis timeouts are
`timeout`. Cancellation propagates unchanged and releases in-flight accounting;
it does not fabricate a completed success/error/timeout observation. Counts are
read individually at scrape time and are not an atomic pool-wide snapshot.

CloudWatch EMF and OpenTelemetry are retained. Prometheus metrics remain active
when EMF is unset or tracing is disabled/sampled out.

## Queries for panels and alerts

```promql
sum by (kind) (rate(market_streamer_market_events_received_total[1m]))
sum by (kind) (rate(market_streamer_market_events_received_bytes_total[1m]))
sum by (stream) (rate(market_http_sse_events_sent_total[1m]))
sum by (stream) (rate(market_http_sse_events_sent_bytes_total[1m]))
sum by (stream) (market_http_sse_connections)
sum by (stream, reason) (rate(market_http_sse_connections_closed_total[5m]))
histogram_quantile(0.95, sum by (stream, le) (rate(market_http_sse_delivery_latency_seconds_bucket[5m])))
sum by (state) (market_http_valkey_pool_connections)
histogram_quantile(0.95, sum by (result, le) (rate(market_http_valkey_pool_acquire_duration_seconds_bucket[5m])))
sum(rate(market_http_valkey_pool_acquire_timeouts_total[5m]))
```

Starting alert expressions to calibrate with Cloud during trading hours:

```promql
market_streamer_market_data_age_seconds > 30
market_streamer_market_data_age_seconds != market_streamer_market_data_age_seconds
up{job=~"market-http|market-streamer"} == 0
absent(up{job="market-streamer"})
sum(rate(market_http_valkey_pool_acquire_timeouts_total[5m])) > 0
```

The NaN comparison detects a feed that has never received an event; `up` detects
a failed scrape and `absent` detects a missing target. Configure alert durations
and schedules externally rather than assuming silence means failure overnight.

## Verification

From inside each service container (or from the same network namespace), using
the default port:

```bash
docker compose -f compose.local.yaml exec market-http python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9100/metrics').read().decode())"
docker compose -f compose.local.yaml exec market-streamer python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9100/metrics').read().decode())"
```

For a running jobs-profile streamer, `exec` targets the persistent service
container; for a one-shot `compose run` container, execute against that container
name instead. Confirm the public API listener returns 404 for `/metrics`.

Open a price and an orderbook SSE connection with available test symbols. Check
active/start counts, both sent counters, and live delivery histogram samples;
disconnect each and check active counts return to their prior values. Stop the
feed while retaining the process and scrape twice to verify increasing age.
Exercise a deliberately small disposable Valkey pool and check immediate
exhaustion records `error`, without becoming a wait or timeout. Do not run
FLUSHDB-capable tests against a useful datastore.

Automated regression tests:

```bash
uv run pytest services/market-http services/market-streamer -ra
uv run ruff check .
uv run ruff format --check .
uv run tach check
uv run tach check-external -e services
```

Set `KTB_TEST_VALKEY_URL` only to a disposable test instance to include the
Valkey/SSE integration tests; they call FLUSHDB. Production scraping, Grafana
panels, alert calibration and live load testing belong to the Cloud handoff.
