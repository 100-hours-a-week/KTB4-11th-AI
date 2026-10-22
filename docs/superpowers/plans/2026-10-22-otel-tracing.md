# OpenTelemetry Tracing for the Price Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trace each sampled price from `market-streamer`'s Valkey publish to the moment `market-http` hands that event to ASGI `send()` for every SSE client, in one trace, with logs carrying `trace_id`/`span_id`.

**Architecture:** `ktb_core.telemetry.tracing()` sets up the OpenTelemetry SDK (OTLP over HTTP, configured by `OTEL_*` env). The streamer opens a `publish` span per price batch and writes `traceparent` + `published_at` into sampled payloads. `market-http` instruments REST with `FastAPIInstrumentor` (stream routes excluded), starts a child `deliver` span per traced Pub/Sub message, and a small ASGI middleware ends it after `send()` returns.

**Tech Stack:** Python 3.13, `opentelemetry-api`/`opentelemetry-sdk`/`opentelemetry-exporter-otlp-proto-http` 1.45, `opentelemetry-instrumentation-fastapi` 0.66b1, FastAPI 0.142 SSE, redis-py asyncio, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-otel-tracing-design.md`

## Global Constraints

- `ktb_core.telemetry` holds only SDK setup: `tracing(service_name)`. No span helpers (`publish_span()`, `sse_span()` etc.). Services call `trace.get_tracer(__name__)` and the propagator directly.
- Configuration only through standard `OTEL_*` env vars; no new pydantic settings.
- Propagation uses `TraceContextTextMapPropagator()` directly in both services, never the global `propagate.inject/extract` (market-http runs with `OTEL_PROPAGATORS=none`).
- No run-level span in `market-streamer`; no span for the SSE snapshot.
- Public REST/SSE payloads and Pydantic schemas do not change.
- Tempo failure or `OTEL_SDK_DISABLED=true` must not affect publishing or streaming.
- `AGENTS.md`: never write code comments or docstrings; no thin wrappers with one caller.
- After any dependency change: `uv lock`, then regenerate requirements with the exact commands in `AGENTS.md` (`uv export --package <svc> ... -o docker/requirements/<svc>.txt` and the `app.txt` command).
- Verified SDK facts (1.45.1): a `service.name` passed in code wins over `OTEL_SERVICE_NAME`; `OTEL_RESOURCE_ATTRIBUTES` still merges; `OTEL_SDK_DISABLED=true` yields `NonRecordingSpan`; an unreachable exporter logs a WARNING per retry and `shutdown()` blocks ~7 s.
- Run tests with `KTB_TEST_VALKEY_URL=redis://localhost:6379/0` (Valkey from `compose.local.yaml`); Valkey tests skip without it.

## Review Focus

1. A client disconnects while traced events are still queued → every queued span is ended, none leak. (Task 4: `test_disconnect_ends_queued_spans`)
2. Keepalive `: ping` chunks arrive between events → they never consume a queued span. (Task 4: `test_keepalive_does_not_end_an_event_span`)
3. A Pub/Sub message whose payload has a malformed `traceparent` → no crash; the event is still delivered, span starts as a new root. (Task 4: `test_malformed_traceparent_still_streams`)
4. Tracing disabled (`OTEL_SDK_DISABLED=true`) → streamer payload is byte-identical to today's. (Task 3: `test_unsampled_publish_payload_is_unchanged`)
5. Stored `price:{code}` carrying the new fields is read back through `GET /prices` → response has no `traceparent`/`published_at`. (Task 4: `test_rest_prices_ignore_trace_fields`)

---

### Task 1: `ktb_core.telemetry` and shared test fixture

**Files:**
- Create: `packages/core/src/ktb_core/telemetry.py`
- Create: `packages/core/tests/test_telemetry.py`
- Modify: `packages/core/pyproject.toml` (dependencies)
- Modify: `conftest.py` (repo root; add fixture)
- Modify: `uv.lock`, every file in `docker/requirements/`

**Interfaces:**
- Produces: `ktb_core.telemetry.tracing(service_name: str) -> contextlib.AbstractContextManager[None]`; module attribute `OTLPSpanExporter` (imported name, patched in tests).
- Produces (root `conftest.py`): fixture `spans` → `InMemorySpanExporter`, cleared, with sampling ON for the duration of the test and OFF otherwise.

- [ ] **Step 1: Add dependencies.** In `packages/core/pyproject.toml` set `dependencies = ["opentelemetry-api>=1.45", "opentelemetry-sdk>=1.45", "opentelemetry-exporter-otlp-proto-http>=1.45"]`. Run `uv lock && uv sync --all-packages --group migrations`. Expected: resolves 1.45.x.

- [ ] **Step 2: Write the failing tests** in `packages/core/tests/test_telemetry.py`:

```python
def test_tracing_names_the_service_and_flushes_on_exit(monkeypatch):
    exporter = InMemorySpanExporter()
    providers = []
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda: exporter)
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", providers.append)
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "deployment.environment=test")

    with tracing("market-streamer"):
        providers[0].get_tracer("t").start_span("publish").end()

    (span,) = exporter.get_finished_spans()
    assert span.resource.attributes["service.name"] == "market-streamer"
    assert span.resource.attributes["deployment.environment"] == "test"


def test_tracing_disabled_creates_no_recording_spans(monkeypatch):
    providers = []
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", providers.append)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    with tracing("svc"):
        assert not providers[0].get_tracer("t").start_span("s").is_recording()


def test_tracing_quiets_exporter_retry_warnings(monkeypatch):
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", lambda provider: None)
    with tracing("svc"):
        assert logging.getLogger("opentelemetry.exporter").getEffectiveLevel() == logging.ERROR
```

- [ ] **Step 3: Run** `uv run pytest packages/core/tests/test_telemetry.py -v`. Expected: FAIL, `ModuleNotFoundError: ktb_core.telemetry`.

- [ ] **Step 4: Implement `tracing(service_name: str)`** as a `@contextmanager`: build `TracerProvider(resource=Resource.create({"service.name": service_name}))`, add `BatchSpanProcessor(OTLPSpanExporter())`, call `trace.set_tracer_provider(provider)`, set `logging.getLogger("opentelemetry.exporter")` to `ERROR`, `yield`, and in `finally` call `provider.shutdown()`. Import `OTLPSpanExporter` from `opentelemetry.exporter.otlp.proto.http.trace_exporter` at module level and `from opentelemetry import trace`. No sampler argument (SDK reads `OTEL_TRACES_SAMPLER`).

- [ ] **Step 5: Run** the same command. Expected: 3 passed.

- [ ] **Step 6: Add the `spans` fixture to root `conftest.py`.** A session-scoped autouse-free setup installs one global `TracerProvider` whose sampler is a small switch (`Sampler` subclass delegating to `ALWAYS_ON` when its `on` flag is set, else `ALWAYS_OFF`) with `SimpleSpanProcessor(InMemorySpanExporter())`; it calls `trace.set_tracer_provider` once per session. The function-scoped `spans` fixture clears the exporter, sets `on = True`, yields the exporter, then sets `on = False`. Global provider can be set only once per process and `ProxyTracer` does not cache the no-op tracer, so this works for module-level tracers in every member.

- [ ] **Step 7: Regenerate requirements.** For each `<svc>` in `market-collector market-http market-streamer market-syncer news-clusterer news-graph-builder news-http news-preprocessor portfolio-builder portfolio-rebalancer report-builder`, run the `AGENTS.md` `uv export --package <svc> ...` command; then the `app.txt` command. Expected: each file gains `opentelemetry-*` pins.

- [ ] **Step 8: Run** `uv run pytest packages/core && uv run ruff check . && uv run ruff format --check .` Expected: pass.

- [ ] **Step 9: Commit**

```bash
git add packages/core conftest.py uv.lock docker/requirements
git commit -m "feat: add shared OpenTelemetry tracing setup to ktb_core"
```

### Task 2: Trace IDs in JSON logs

**Files:**
- Modify: `packages/core/src/ktb_core/logging.py` (`JsonFormatter.format`)
- Test: `packages/core/tests/test_logging.py`

**Interfaces:**
- Consumes: `spans` fixture (Task 1).
- Produces: log JSON fields `trace_id` (32 lowercase hex) and `span_id` (16 lowercase hex), present only when a valid span is current.

- [ ] **Step 1: Write the failing tests:**

```python
def test_lines_inside_a_span_carry_trace_and_span_ids(capsys, spans):
    setup_logging("INFO", service_name="svc")
    with trace.get_tracer("t").start_as_current_span("work") as span:
        get_logger("svc").info("inside")
    payload = json.loads(capsys.readouterr().out.strip())
    context = span.get_span_context()
    assert payload["trace_id"] == format(context.trace_id, "032x")
    assert payload["span_id"] == format(context.span_id, "016x")


def test_lines_outside_a_span_have_no_trace_fields(capsys):
    setup_logging("INFO", service_name="svc")
    get_logger("svc").info("outside")
    payload = json.loads(capsys.readouterr().out.strip())
    assert "trace_id" not in payload and "span_id" not in payload
```

- [ ] **Step 2: Run** `uv run pytest packages/core/tests/test_logging.py -v`. Expected: first test FAILs with `KeyError: 'trace_id'`.

- [ ] **Step 3: Implement.** In `JsonFormatter.format`, after the base fields, read `trace.get_current_span().get_span_context()`; if `is_valid`, set `trace_id` with `trace.format_trace_id` and `span_id` with `trace.format_span_id`. `start_logging` is unchanged (keeps `elapsed_ms`).

- [ ] **Step 4: Run** the same command. Expected: all pass (including the existing `"elapsed_ms" in finish`).

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat: add trace and span ids to JSON logs"
```

### Task 3: market-streamer publish span and trace fields

**Files:**
- Modify: `services/market-streamer/src/market_streamer/stream.py` (`publish`)
- Modify: `services/market-streamer/src/market_streamer/__main__.py`
- Modify: `services/market-streamer/pyproject.toml` (add `"opentelemetry-api>=1.45"`; deptry flags transitive imports)
- Test: `services/market-streamer/tests/test_stream.py`, `services/market-streamer/tests/test_run.py` or a new `test_main.py`
- Modify: `uv.lock`, `docker/requirements/market-streamer.txt`, `docker/requirements/app.txt`

**Interfaces:**
- Consumes: `tracing` (Task 1), `spans` fixture.
- Produces: span name `"publish"`, kind `SpanKind.PRODUCER`, attributes `prices.received: int`, `prices.accepted: int`. Sampled price payloads gain `"traceparent": str` (W3C) and `"published_at": str` (`datetime.now(UTC).isoformat()`, taken just before the write pipeline executes). Same payload for `SET` and `PUBLISH`.

- [ ] **Step 1: Write the failing tests** in `test_stream.py` (reuse the existing `PRICE`/`TICK` fixtures and the pattern of `test_publish_sets_and_publishes_each_price`):

```python
def test_sampled_publish_adds_trace_fields_from_the_publish_span(valkey_url, spans):
    message, stored = asyncio.run(_publish_and_capture(valkey_url, [PRICE]))
    (span,) = spans.get_finished_spans()
    context = span.get_span_context()
    payload = json.loads(message["data"])
    assert span.name == "publish" and span.kind == SpanKind.PRODUCER
    assert span.attributes["prices.received"] == 1
    assert span.attributes["prices.accepted"] == 1
    assert payload["traceparent"] == f"00-{context.trace_id:032x}-{context.span_id:016x}-01"
    assert datetime.fromisoformat(payload["published_at"]).tzinfo is not None
    assert json.loads(stored) == payload


def test_unsampled_publish_payload_is_unchanged(valkey_url):
    message, stored = asyncio.run(_publish_and_capture(valkey_url, [PRICE]))
    expected = {k: v for k, v in PRICE.items() if k != "trade_value"}
    assert json.loads(message["data"]) == expected
    assert json.loads(stored) == expected
```

`_publish_and_capture(valkey_url, prices)` is a test helper that subscribes to `price:<code>`, calls `publish`, and returns `(message, stored)` (same shape as the existing orderbook test).

- [ ] **Step 2: Run** `uv run pytest services/market-streamer/tests/test_stream.py -v`. Expected: sampled test FAILs (no span / no `traceparent`); unsampled passes.

- [ ] **Step 3: Implement.** Module-level `tracer = trace.get_tracer(__name__)` and `propagator = TraceContextTextMapPropagator()`. Wrap the body of `publish` in `with tracer.start_as_current_span("publish", kind=SpanKind.PRODUCER) as span:`; set both attributes; when `span.is_recording()`, build `trace_fields = {"published_at": datetime.now(UTC).isoformat()}` and `propagator.inject(trace_fields)` once per batch, right before the write pipeline, and merge `trace_fields` into each payload dict. `publish_orderbooks` is unchanged.

- [ ] **Step 4: Run** the same command plus `uv run pytest services/market-streamer`. Expected: all pass.

- [ ] **Step 5: Wire `main()`.** Wrap the existing `with start_logging(...)` block in `with tracing("market-streamer"):` (tracing outermost). Add a test that monkeypatches `Settings`, `setup_logging`, `run` (no-op coroutine) and `tracing` (records the name, returns `nullcontext()`), calls `main()`, and asserts the recorded name is `"market-streamer"`.

- [ ] **Step 6: Add the dependency, lock, regenerate** `market-streamer.txt` and `app.txt`. Run `uv run deptry services/market-streamer/src --config services/market-streamer/pyproject.toml`. Expected: no issues.

- [ ] **Step 7: Commit**

```bash
git add services/market-streamer uv.lock docker/requirements
git commit -m "feat: trace market-streamer price publishes"
```

### Task 4: market-http REST instrumentation and SSE deliver spans

**Files:**
- Create: `services/market-http/src/market_http/tracing.py`
- Modify: `services/market-http/src/market_http/app.py` (`create_app`)
- Modify: `services/market-http/src/market_http/controllers/prices.py` (`stream_prices`)
- Modify: `services/market-http/src/market_http/__main__.py`
- Modify: `services/market-http/pyproject.toml` (add `"opentelemetry-api>=1.45"`, `"opentelemetry-instrumentation-fastapi>=0.66b1"`)
- Test: create `services/market-http/tests/test_tracing.py`; modify `services/market-http/tests/test_main.py`, `test_prices.py`
- Modify: `uv.lock`, `docker/requirements/market-http.txt`, `docker/requirements/app.txt`

**Interfaces:**
- Consumes: `tracing` (Task 1), `spans` fixture; streamer payload fields `traceparent`, `published_at` (Task 3).
- Produces: `market_http.tracing.PENDING_SPANS: ContextVar[deque[Span | None] | None]` (default `None`); `market_http.tracing.SSESendSpans` (pure ASGI middleware, `__init__(self, app)`). Span name `"deliver"`, kind `SpanKind.CONSUMER`, parent = context extracted from the message's `traceparent`; attributes `code: str`, `sse.connection_id: str` (uuid4 hex, one per stream call), `published_at: str`, `publish_to_receive_ms: float` (T1−T0).

Middleware algorithm (the one non-obvious piece):

```python
async def __call__(self, scope, receive, send):
    if scope["type"] != "http" or not scope["path"].endswith("/stream"):
        return await self.app(scope, receive, send)
    pending = deque()
    token = PENDING_SPANS.set(pending)

    async def send_then_end(message):
        await send(message)
        body = message.get("body", b"")
        if message["type"] == "http.response.body" and body and not body.startswith(b":") and pending:
            span = pending.popleft()
            if span is not None:
                span.end()

    try:
        await self.app(scope, receive, send_then_end)
    finally:
        PENDING_SPANS.reset(token)
        for span in pending:
            if span is not None:
                span.end()
```

Handler rule: `pending = PENDING_SPANS.get()`. If `pending is None` (called outside the app, e.g. existing direct tests), behave exactly as today. Otherwise append `None` before yielding each snapshot event; for each Pub/Sub message, if `'"traceparent"'` is in the raw data, `json.loads` it, start the `deliver` span with `context=TraceContextTextMapPropagator().extract(fields)`, append it, then validate and yield; else append `None` and yield. Append before validation so a validation error cannot leak an unended span.

- [ ] **Step 1: Write the failing tests** in `test_tracing.py`. Do not use `httpx.ASGITransport` or `TestClient.stream` for `/prices/stream`: both wait for the full body and an SSE stream never ends. Write one helper, `_drive_stream(app, query, on_body) -> list[dict]`, that calls the ASGI app directly with an `http` scope for `GET /prices/stream`; its `receive` returns `{"type": "http.request"}` once and then waits on an `asyncio.Event` before returning `{"type": "http.disconnect"}`; its `send` records messages and calls `on_body(chunk)` for each body chunk, and the test sets the event once it has what it needs (Starlette's `StreamingResponse` cancels the stream on disconnect). Drive the real app over Valkey (monkeypatch `prices_module.questdb_prices` and `current_day_price_limits` as `test_prices.py` does) and publish payloads to `price:005930` after the snapshot chunk arrives:

```python
def test_traced_message_gets_a_deliver_span_under_the_publish_span(valkey_url, spans): ...
    # publish a payload with traceparent "00-<T>-<P>-01" and published_at
    # read one live SSE event from the response
    # (deliver,) = [s for s in spans.get_finished_spans() if s.name == "deliver"]
    assert deliver.parent.span_id == int(P, 16)
    assert deliver.context.trace_id == int(T, 16)
    assert deliver.kind == SpanKind.CONSUMER
    assert deliver.attributes["code"] == "005930"
    assert deliver.attributes["publish_to_receive_ms"] >= 0
    assert deliver.attributes["sse.connection_id"]

def test_untraced_message_gets_no_span(valkey_url, spans): ...
    assert not [s for s in spans.get_finished_spans() if s.name == "deliver"]

def test_keepalive_does_not_end_an_event_span(spans): ...
    # unit-test SSESendSpans with a fake app: set two pending spans, send b": ping\n\n" then one event chunk
    # assert exactly one span ended, the first one

def test_disconnect_ends_queued_spans(spans): ...
    # fake app appends two spans then raises/returns without sending bodies
    # assert both spans are in spans.get_finished_spans()

def test_malformed_traceparent_still_streams(valkey_url, spans): ...
    # payload with "traceparent": "garbage"; the SSE event is still delivered with the price

def test_rest_requests_get_server_spans_and_stream_routes_do_not(valkey_url, spans): ...
    # TestClient GET /health → a SERVER span exists; _drive_stream on /prices/stream → no SERVER span

def test_rest_prices_ignore_trace_fields(valkey_url): ...
    # SET price:005930 with traceparent/published_at; GET /prices → response JSON has neither key
```

Each `...` is the implementer's setup; the asserts shown are the contract. Also assert in `test_traced_message...` that the SSE `data:` JSON has no `traceparent`/`published_at`.

- [ ] **Step 2: Run** `uv run pytest services/market-http/tests/test_tracing.py -v`. Expected: FAIL, `ModuleNotFoundError: market_http.tracing`.

- [ ] **Step 3: Implement** `tracing.py` (algorithm above), the handler rule in `stream_prices`, and in `create_app`: `app.add_middleware(SSESendSpans)` and `FastAPIInstrumentor.instrument_app(app, excluded_urls="prices/stream,orderbooks/stream")`. Excluding in code (not env) is a builder's call: it protects the "no connection-long trace" anti-goal even if deployment forgets the variable.

- [ ] **Step 4: Run** `uv run pytest services/market-http -v`. Expected: all pass, including existing direct `stream_prices(...)` tests.

- [ ] **Step 5: Wire `main()`** as `with tracing("market-http"): uvicorn.run(...)`. In `test_main.py`, monkeypatch `entry.tracing` to record the name and return `nullcontext()`; assert `"market-http"`.

- [ ] **Step 6: Add dependencies, lock, regenerate** `market-http.txt` and `app.txt`; run deptry for `services/market-http`. Expected: no issues.

- [ ] **Step 7: Commit**

```bash
git add services/market-http uv.lock docker/requirements
git commit -m "feat: trace market-http SSE price delivery"
```

### Task 5: Deployment wiring

**Files:**
- Modify: `compose.local.yaml`, `compose.prod.yaml` (`market-http` and `market-streamer` `environment:` blocks)
- Modify: `infrastructure/ecs/market-http.json` (`environment`)
- Modify: `.env.example`, `AGENTS.md` (Environment variables table)

**Interfaces:**
- Produces: host-side variables `MARKET_STREAMER_OTEL_*` / `MARKET_HTTP_OTEL_*` mapped to standard names inside each container.

- [ ] **Step 1: Compose.** In both compose files, add to `market-streamer`:
  `OTEL_SDK_DISABLED: ${MARKET_STREAMER_OTEL_SDK_DISABLED:-true}`, `OTEL_EXPORTER_OTLP_ENDPOINT: ${MARKET_STREAMER_OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}`, `OTEL_TRACES_SAMPLER: parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG: ${MARKET_STREAMER_OTEL_TRACES_SAMPLER_ARG:-0.1}`, `OTEL_RESOURCE_ATTRIBUTES: ${MARKET_STREAMER_OTEL_RESOURCE_ATTRIBUTES:-deployment.environment=local}`.
  Add the same five with the `MARKET_HTTP_` prefix to `market-http` (sampler arg default `0.01`) plus fixed `OTEL_PROPAGATORS: none`.

- [ ] **Step 2: ECS.** Add to `infrastructure/ecs/market-http.json`: `OTEL_SDK_DISABLED=true`, `OTEL_PROPAGATORS=none`, `OTEL_TRACES_SAMPLER=parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG=0.01`, `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod`.

- [ ] **Step 3: Document** the host-side variables in `.env.example` (commented, disabled by default) and in the `AGENTS.md` env table.

- [ ] **Step 4: Verify** `docker compose -f compose.local.yaml config --quiet` and `docker compose -f compose.prod.yaml --env-file .env.example config --quiet` (or the CI equivalent) succeed; `python -m json.tool infrastructure/ecs/market-http.json >/dev/null` succeeds.

- [ ] **Step 5: Commit**

```bash
git add compose.local.yaml compose.prod.yaml infrastructure/ecs/market-http.json .env.example AGENTS.md
git commit -m "chore: wire OpenTelemetry settings for market services"
```

### Task 6: Overhead measurement and documentation

**Files:**
- Create: `scripts/bench-publish-tracing.py`
- Create: `docs/operations/2026-10-22-price-stream-tracing.md`
- Modify: `AGENTS.md` (Architecture bullets)

**Interfaces:**
- Consumes: `market_streamer.stream.publish`, `parse_tick`; `KTB_TEST_VALKEY_URL`.

- [ ] **Step 1: Write the benchmark.** `scripts/bench-publish-tracing.py MODE` with `MODE` in `off | sampled-out | sampled-in`. It installs a `TracerProvider` with `SimpleSpanProcessor(InMemorySpanExporter())` and sampler `ALWAYS_OFF`/`ALWAYS_ON` (no provider for `off`), builds 26 distinct prices (one batch, codes `000001`…`000026`, increasing volume per round), warms up 50 publishes, then times 1000 `publish()` calls against `KTB_TEST_VALKEY_URL` (flushdb first) and prints `mode`, mean and p95 ms per call, and process max RSS (`resource.getrusage`). Each mode is a separate process run.

- [ ] **Step 2: Run** all three modes:

```bash
for m in off sampled-out sampled-in; do KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run python scripts/bench-publish-tracing.py $m; done
```

Expected: three result lines; record them.

- [ ] **Step 3: Write `docs/operations/2026-10-22-price-stream-tracing.md`:** the trace shape (`publish` → `deliver` per client); T0/T1/T2 definitions and caveats (cross-host clock sync for `publish_to_receive_ms`; T0 is before the pipeline executes, not Valkey's `PUBLISH`; T2 is `send()` returning, not network delivery; `deliver` duration includes waiting in FastAPI's SSE buffers); sampling (streamer ratio drives market-http event spans; REST sampled locally; TraceQL results are samples); the variables the AWS test system must set for both services; benchmark results from Step 2 including the ~85-byte payload growth for sampled messages.

- [ ] **Step 4: Update `AGENTS.md` Architecture** with one bullet: logs are for operational events and spans are for durations; `ktb_core.telemetry.tracing(name)` wraps a service's main work (sync: around the work; async: around `asyncio.run`); services use `trace.get_tracer(__name__)` directly; never open a span that lives for a whole long-running process or connection; `start_logging`'s `elapsed_ms` is deprecated and is removed once every `start_logging` service has an equivalent span; other ad-hoc timers (`market-collector/store.py`, portfolio-builder `Stopwatch`, `infrastructure/questdb/inspect_checkpoint.py`) are candidates to move to spans. Also amend the `ktb_core` "no third-party core dependencies" wording to mention the OpenTelemetry dependencies.

- [ ] **Step 5: Verify** `uv run ruff check . && uv run ruff format --check . && KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run pytest`. Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/bench-publish-tracing.py docs/operations/2026-10-22-price-stream-tracing.md AGENTS.md
git commit -m "docs: document price stream tracing and publish overhead"
```
