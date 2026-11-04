# Market Prometheus Implementation Plan

> Execute inline with superpowers:executing-plans; use failing tests before implementation.

Goal: implement issue #392 without changing SSE, tracing, EMF, or pool behavior.
Architecture: independent per-process registries and private metrics listeners.
Tech stack: Python 3.13, prometheus-client, FastAPI ASGI, redis.asyncio.
Spec: ../specs/2026-10-23-market-prometheus-design.md

## Global constraints

Use MARKET_HTTP_PROMETHEUS_BIND_HOST and MARKET_STREAMER_PROMETHEUS_BIND_HOST,
default 127.0.0.1. Keep existing nonblocking pool behavior and existing telemetry.
Labels must use only the bounded values in the issue's metric contract.

## Review focus

Failed sends must not count events; rejected HTTP requests must not count connections.
Initial snapshots must not produce Pub/Sub latency observations.
Cancelled pool requests must release in-flight accounting without masking cancellation.
Feed silence must increase age at scrape time; raw rejected events still count.
Metrics listeners must close on normal and exceptional exit.

## Task 1: HTTP observations and pool

Files: market_http/observability/prometheus.py, observability/pool.py,
app.py, valkey.py, controllers/prices.py, controllers/orderbooks.py;
tests/test_prometheus.py. Introduce MarketMetrics(registry), SSEMetrics middleware,
track_delivery(live=False), and observe_pool(pool, metrics). The middleware uses per-request
pending receipt timestamps, complete SSE frames, and successful ASGI sends.

- [x] Write failing tests for connection outcomes, split frames, latency and pool outcomes.
- [x] Run the focused tests and confirm missing observations.
- [x] Implement bounded collectors, SSE observer and existing-pool acquisition observer; wire controllers/app.
- [x] Run tests and existing EMF/tracing tests.

## Task 2: Streamer observations and listener configuration

Files: market_streamer/observability/metrics.py, stream.py, both settings.py and
__main__.py files, tests/test_prometheus.py in both services, existing entry tests.
Introduce MarketMetrics.receive(entries) and scrape-time age callbacks. Both metrics
objects expose a listener context manager with server shutdown and thread join.

- [x] Add failing tests for raw entry counts/bytes, silence age and binding configuration.
- [x] Implement receive hook before parsing/publication; wire listener lifecycle.
- [x] Run both service suites.

## Task 3: Packaging and operational handoff

Files: both pyproject.toml files, uv.lock, three affected requirements exports,
.env.example, compose.local.yaml, compose.prod.yaml, operations documentation.

- [x] Declare prometheus-client, lock and regenerate exact requirement exports.
- [x] Wire bind-host and port environment variables, preserving loopback defaults.
- [x] Document the full metric contract, private scraping setup, PromQL and smoke checks.
- [x] Run Ruff, Tach, full pytest and feasible isolated imports/image checks.
- [ ] Commit, push issue branch, create PR against dev.

## Validation results

- Final full suite with disposable Valkey: 924 passed, 216 skipped (PostgreSQL/QuestDB unconfigured).
- Affected services with disposable Valkey: 162 passed.
- Ruff lint/format and Tach module/external dependency checks passed.
- Independent review found root-path and CORS preflight gaps; both reproduced, fixed, retested, and re-reviewed clean.
- User refinement: pool state label is acquiring, replacing waiting.
- Pool instrumentation wraps the existing instance instead of constructing a subclass, preserving all Redis defaults and IAM/TLS configuration.
