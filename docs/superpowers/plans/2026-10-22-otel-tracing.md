# OpenTelemetry Tracing for the Price Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trace each sampled price from `market-streamer`'s Valkey publish to the moment `market-http` hands that event to ASGI `send()` for every SSE client, in one trace, with logs carrying `trace_id`/`span_id`.

**Architecture:** A new workspace package `ktb_telemetry` (`packages/telemetry`, following `ktb_aws`) provides `tracing(service_name)`: OpenTelemetry SDK setup (OTLP over HTTP, configured by `OTEL_*` env) plus a logging filter that adds trace ids to the existing JSON logs. The streamer opens a `publish` span per price batch and writes `traceparent` + `published_at` into sampled payloads. `market-http` instruments REST with `FastAPIInstrumentor` (stream routes excluded), starts a child `deliver` span per traced Pub/Sub message, and a small ASGI middleware ends it after `send()` returns.

**Tech Stack:** Python 3.13, `opentelemetry-api`/`opentelemetry-sdk`/`opentelemetry-exporter-otlp-proto-http` 1.45, `opentelemetry-instrumentation-fastapi` 0.66b1, FastAPI 0.142 SSE, redis-py asyncio, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-otel-tracing-design.md`

## Global Constraints

- `ktb_core` gets no third-party dependency and `ktb_core.logging` is not modified (`AGENTS.md`: keep core free of third-party dependencies). Issue #368's `ktb_core/telemetry.py` becomes `ktb_telemetry` (user decision).
- `ktb_telemetry` holds only SDK setup and its log filter: `tracing(service_name)`. No span helpers (`publish_span()`, `sse_span()` etc.). Services call `trace.get_tracer(__name__)` and the propagator directly.
- Only `market-streamer` and `market-http` depend on `ktb-telemetry`. No other service, Dockerfile, or requirements file changes.
- Configuration only through standard `OTEL_*` env vars; no new pydantic settings. Exceptions fixed in code: exporter timeout 2 s; stream routes excluded from FastAPI instrumentation.
- Propagation uses `TraceContextTextMapPropagator()` directly in both services, never the global `propagate.inject/extract` (market-http runs with `OTEL_PROPAGATORS=none`).
- No run-level span in `market-streamer`; no span for the SSE snapshot.
- Public REST/SSE payloads and Pydantic schemas do not change.
- Tempo failure or `OTEL_SDK_DISABLED=true` must not affect publishing, streaming, or logging.
- `AGENTS.md`: do not write new code comments or docstrings; no single-caller wrappers.
- After any dependency change: `uv lock`, then regenerate requirements with the exact `AGENTS.md` commands, for `market-streamer`, `market-http`, and `app.txt` only.
- Verified SDK facts (1.45.1): a `service.name` passed in code wins over `OTEL_SERVICE_NAME`; `OTEL_RESOURCE_ATTRIBUTES` still merges; `OTEL_SDK_DISABLED=true` yields `NonRecordingSpan`; with the default exporter timeout an unreachable endpoint blocks `shutdown()` ~6.5 s, with `timeout=2` 1.2 s (refused) / 2.1 s (blackholed).
- Verified misconfiguration behavior: a bogus `OTEL_TRACES_SAMPLER`, non-numeric `OTEL_TRACES_SAMPLER_ARG`, malformed `OTEL_EXPORTER_OTLP_ENDPOINT`, or garbage `OTEL_RESOURCE_ATTRIBUTES` logs a WARNING and falls back; nothing raises. `OTEL_SDK_DISABLED` disables only on `true` (`yes`/`1` leave tracing on) — say so in `.env.example` and the ops doc.
- Verified logging facts: a filter must sit on the root **handlers** (a root-logger filter misses child loggers); a log record factory is overwritten by `extra={"fields": ...}`. A handler filter that merges into `record.fields` works for `StructuredLogger`, plain stdlib loggers, exceptions, and `start_logging` lines.
- Run tests with `KTB_TEST_VALKEY_URL=redis://localhost:6379/0` (Valkey from `compose.local.yaml`); Valkey tests skip without it.

## Review Focus

1. A client disconnects while traced events are still queued → every queued span is ended, none leak. (Task 3: `test_disconnect_ends_queued_spans`)
2. Keepalive `: ping` chunks arrive between events → they never consume a queued span. (Task 3: `test_keepalive_does_not_end_an_event_span`)
3. A Pub/Sub message with a malformed `traceparent` → no crash; the event is still delivered. (Task 3: `test_malformed_traceparent_still_streams`)
4. Tempo unreachable → `tracing()` exits without raising and within ~3 s; app code is unaffected. (Task 1: `test_unreachable_exporter_neither_raises_nor_stalls`)
5. Stored `price:{code}` carrying the new fields is read back through `GET /prices` → response has no `traceparent`/`published_at`. (Task 3: `test_rest_prices_ignore_trace_fields`)

---

### Task 1: `ktb_telemetry` package and shared test fixture

**Files:**
- Create: `packages/telemetry/pyproject.toml`, `packages/telemetry/src/ktb_telemetry/__init__.py` (empty), `packages/telemetry/src/ktb_telemetry/tracing.py`
- Create: `packages/telemetry/tests/test_tracing.py`
- Modify: `tach.toml` (source root `packages/telemetry/src`; module `ktb_telemetry` with no `depends_on`)
- Modify: `conftest.py` (repo root; add fixture)
- Modify: `uv.lock`

**Interfaces:**
- Produces: `ktb_telemetry.tracing.tracing(service_name: str) -> contextlib.AbstractContextManager[None]`; module attribute `OTLPSpanExporter` (imported name, patched in tests); `ktb_telemetry.tracing.TraceContextFilter(logging.Filter)`.
- Produces (root `conftest.py`): fixture `spans` → `InMemorySpanExporter`, cleared, with sampling ON for the duration of the test and OFF otherwise.

- [ ] **Step 1: Create the package.** `packages/telemetry/pyproject.toml` mirrors `packages/aws/pyproject.toml`: `name = "ktb-telemetry"`, description "Shared OpenTelemetry tracing setup for AI services", `dependencies = ["opentelemetry-api>=1.45", "opentelemetry-sdk>=1.45", "opentelemetry-exporter-otlp-proto-http>=1.45"]`, same build system. Add it to `tach.toml`. Run `uv lock && uv sync --all-packages --group migrations`. Expected: resolves 1.45.x; workspace glob `packages/*` picks the member up.

- [ ] **Step 2: Write the failing tests** in `packages/telemetry/tests/test_tracing.py`:

```python
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
```

`setup_logging`/`get_logger` come from `ktb_core.logging` (test-only import; available in the workspace venv).

- [ ] **Step 3: Run** `uv run pytest packages/telemetry -v`. Expected: FAIL, `ModuleNotFoundError: ktb_telemetry.tracing`.

- [ ] **Step 4: Implement** in `ktb_telemetry/tracing.py`:
  - `TraceContextFilter.filter(record) -> bool`: if `trace.get_current_span().get_span_context().is_valid`, set `record.fields` to the existing `fields` dict (or `{}` when absent or not a dict) plus `trace_id` (`trace.format_trace_id`) and `span_id` (`trace.format_span_id`). Always return `True`.
  - `tracing(service_name)` as a `@contextmanager`: `TracerProvider(resource=Resource.create({"service.name": service_name}))` with `BatchSpanProcessor(OTLPSpanExporter(timeout=2))`; `trace.set_tracer_provider(provider)`; `logging.getLogger("opentelemetry.exporter").setLevel(logging.ERROR)`; add one `TraceContextFilter` to each handler in `list(logging.getLogger().handlers)`; `yield`; in `finally`, remove the filter from those handlers, then `provider.shutdown()`. No sampler argument (the SDK reads `OTEL_TRACES_SAMPLER`). Import `OTLPSpanExporter` from `opentelemetry.exporter.otlp.proto.http.trace_exporter` at module level.

- [ ] **Step 5: Run** the same command. Expected: 9 passed.

- [ ] **Step 6: Add the `spans` fixture to root `conftest.py`.** A session-scoped fixture installs one global `TracerProvider` once per process, with `SimpleSpanProcessor(InMemorySpanExporter())` and a switch sampler (a `Sampler` subclass delegating to `ALWAYS_ON` while its `on` flag is set, else `ALWAYS_OFF`). The function-scoped `spans` fixture depends on it, clears the exporter, sets `on = True`, yields the exporter, then sets `on = False`. This works because the global provider can be set only once per process and `ProxyTracer` does not cache the no-op tracer, so module-level tracers in every member pick it up. Root `conftest.py` is loaded for every member's tests: keep all OpenTelemetry imports inside the fixture bodies so collection never fails in an environment without the SDK.

- [ ] **Step 7: Run** `uv run pytest packages && uv run ruff check . && uv run ruff format --check . && uv run tach check && uv run deptry packages/telemetry/src --config packages/telemetry/pyproject.toml`. Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add packages/telemetry conftest.py tach.toml uv.lock
git commit -m "feat: add ktb_telemetry tracing setup with log trace ids"
```

### Task 2: market-streamer publish span and trace fields

**Files:**
- Modify: `services/market-streamer/src/market_streamer/stream.py` (`publish`)
- Modify: `services/market-streamer/src/market_streamer/__main__.py`
- Modify: `services/market-streamer/pyproject.toml` (add `"ktb-telemetry"` with `[tool.uv.sources] ktb-telemetry = { workspace = true }`, and `"opentelemetry-api>=1.45"` for direct imports)
- Modify: `docker/market-streamer.Dockerfile` (`COPY packages/telemetry packages/telemetry`; add `./packages/telemetry` to the `--no-deps` install), `docker/app.Dockerfile` (add `./packages/telemetry` to its install list), `tach.toml` (`market_streamer` depends on `ktb_telemetry`)
- Test: `services/market-streamer/tests/test_stream.py`; new `services/market-streamer/tests/test_main.py`
- Modify: `uv.lock`, `docker/requirements/market-streamer.txt`, `docker/requirements/app.txt`

**Interfaces:**
- Consumes: `ktb_telemetry.tracing.tracing` (Task 1), `spans` fixture.
- Produces: span name `"publish"`, kind `SpanKind.PRODUCER`, attributes `prices.received: int`, `prices.accepted: int`. Sampled price payloads gain `"traceparent": str` (W3C) and `"published_at": str` (`datetime.now(UTC).isoformat()`, taken just before the write pipeline executes). Same payload for `SET` and `PUBLISH`.

- [ ] **Step 1: Write the failing tests** in `test_stream.py` (reuse the existing `PRICE`/`TICK` data and the pattern of `test_publish_orderbooks_sets_and_publishes_identical_payload`):

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

`_publish_and_capture(valkey_url, prices)` subscribes to `price:<code>`, calls `publish`, and returns `(message, stored)`.

- [ ] **Step 2: Run** `uv run pytest services/market-streamer/tests/test_stream.py -v`. Expected: the sampled test FAILs (no span, no `traceparent`); the unsampled test passes.

- [ ] **Step 3: Implement.** Module-level `tracer = trace.get_tracer(__name__)` and `propagator = TraceContextTextMapPropagator()`. Wrap the body of `publish` in `with tracer.start_as_current_span("publish", kind=SpanKind.PRODUCER) as span:`; set both attributes; when `span.is_recording()`, build `trace_fields = {"published_at": datetime.now(UTC).isoformat()}` and `propagator.inject(trace_fields)` once per batch, right before the write pipeline, and merge `trace_fields` into each payload dict. `publish_orderbooks` is unchanged.

- [ ] **Step 4: Run** `uv run pytest services/market-streamer`. Expected: all pass.

- [ ] **Step 5: Wire `main()`.** After `setup_logging(...)`, wrap the existing `with start_logging(...)` block in `with tracing("market-streamer"):` (`setup_logging` must run first so the filter attaches to its handler). In `test_main.py`, monkeypatch `Settings`, `setup_logging`, `run` (no-op coroutine) and `tracing` (records the name, returns `nullcontext()`), call `main()`, and assert the recorded name is `"market-streamer"` and that `setup_logging` was called before `tracing`.

- [ ] **Step 6: Wire packaging.** Apply the pyproject, Dockerfile, `app.Dockerfile`, and `tach.toml` changes; `uv lock`; regenerate `market-streamer.txt` and `app.txt` with the `AGENTS.md` commands. Run `uv run deptry services/market-streamer/src --config services/market-streamer/pyproject.toml && uv run tach check`. Expected: no issues.

- [ ] **Step 7: Commit**

```bash
git add services/market-streamer docker tach.toml uv.lock
git commit -m "feat: trace market-streamer price publishes"
```

### Task 3: market-http REST instrumentation and SSE deliver spans

**Files:**
- Create: `services/market-http/src/market_http/tracing.py`
- Modify: `services/market-http/src/market_http/app.py` (`create_app`)
- Modify: `services/market-http/src/market_http/controllers/prices.py` (`stream_prices`)
- Modify: `services/market-http/src/market_http/__main__.py`
- Modify: `services/market-http/pyproject.toml` (add `"ktb-telemetry"` with workspace source, `"opentelemetry-api>=1.45"`, `"opentelemetry-instrumentation-fastapi>=0.66b1"`)
- Modify: `docker/market-http.Dockerfile` (COPY + install `./packages/telemetry`), `tach.toml` (`market_http` depends on `ktb_telemetry`)
- Test: create `services/market-http/tests/test_tracing.py`; modify `services/market-http/tests/test_main.py`
- Modify: `uv.lock`, `docker/requirements/market-http.txt`, `docker/requirements/app.txt`

**Interfaces:**
- Consumes: `ktb_telemetry.tracing.tracing` (Task 1), `spans` fixture; streamer payload fields `traceparent`, `published_at` (Task 2).
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
    # publish a payload with traceparent "00-<T>-<P>-01" and published_at; read one live SSE event
    (deliver,) = [s for s in spans.get_finished_spans() if s.name == "deliver"]
    assert deliver.parent.span_id == int(P, 16)
    assert deliver.context.trace_id == int(T, 16)
    assert deliver.kind == SpanKind.CONSUMER
    assert deliver.attributes["code"] == "005930"
    assert deliver.attributes["publish_to_receive_ms"] >= 0
    assert deliver.attributes["sse.connection_id"]
    # and the SSE data: JSON has no "traceparent" / "published_at"

def test_untraced_message_gets_no_span(valkey_url, spans): ...
    assert not [s for s in spans.get_finished_spans() if s.name == "deliver"]

def test_keepalive_does_not_end_an_event_span(spans): ...
    # SSESendSpans around a fake app that appends two spans to PENDING_SPANS,
    # sends b": ping\n\n" then one event chunk; exactly the first span has ended

def test_disconnect_ends_queued_spans(spans): ...
    # fake app appends two spans and returns without sending bodies; both spans are finished

def test_malformed_traceparent_still_streams(valkey_url, spans): ...
    # payload with "traceparent": "garbage"; the SSE event is still delivered with the price

def test_rest_requests_get_server_spans_and_stream_routes_do_not(valkey_url, spans): ...
    # TestClient GET /health → a SERVER span exists; _drive_stream on /prices/stream → no SERVER span

def test_rest_prices_ignore_trace_fields(valkey_url): ...
    # SET price:005930 with traceparent/published_at; GET /prices → JSON has neither key
```

Each `...` is the implementer's setup; the asserts shown are the contract.

- [ ] **Step 2: Run** `uv run pytest services/market-http/tests/test_tracing.py -v`. Expected: FAIL, `ModuleNotFoundError: market_http.tracing`.

- [ ] **Step 3: Implement** `market_http/tracing.py` (algorithm above), the handler rule in `stream_prices`, and in `create_app`: `app.add_middleware(SSESendSpans)` and `FastAPIInstrumentor.instrument_app(app, excluded_urls="prices/stream,orderbooks/stream")`.

- [ ] **Step 4: Run** `uv run pytest services/market-http -v`. Expected: all pass, including the existing direct `stream_prices(...)` tests.

- [ ] **Step 5: Wire `main()`** as `with tracing("market-http"): uvicorn.run(...)` after `setup_logging(...)`. In `test_main.py`, monkeypatch `entry.tracing` to record the name and return `nullcontext()`; assert `"market-http"`.

- [ ] **Step 6: Wire packaging.** Apply the pyproject, Dockerfile, and `tach.toml` changes; `uv lock`; regenerate `market-http.txt` and `app.txt`; run `uv run deptry services/market-http/src --config services/market-http/pyproject.toml && uv run tach check`. Expected: no issues.

- [ ] **Step 7: Commit**

```bash
git add services/market-http docker tach.toml uv.lock
git commit -m "feat: trace market-http SSE price delivery"
```

### Task 4: Deployment wiring

**Files:**
- Modify: `compose.local.yaml`, `compose.prod.yaml` (`market-http` and `market-streamer` `environment:` blocks)
- Modify: `infrastructure/ecs/market-http.json` (`environment`)
- Modify: `.env.example`

**Interfaces:**
- Produces: host-side variables `MARKET_STREAMER_OTEL_*` / `MARKET_HTTP_OTEL_*` mapped to standard names inside each container.

- [ ] **Step 1: Compose.** In both compose files, add to `market-streamer`:
  `OTEL_SDK_DISABLED: ${MARKET_STREAMER_OTEL_SDK_DISABLED:-true}`, `OTEL_EXPORTER_OTLP_ENDPOINT: ${MARKET_STREAMER_OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}`, `OTEL_TRACES_SAMPLER: parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG: ${MARKET_STREAMER_OTEL_TRACES_SAMPLER_ARG:-0.1}`, `OTEL_RESOURCE_ATTRIBUTES: ${MARKET_STREAMER_OTEL_RESOURCE_ATTRIBUTES:-deployment.environment=local}`.
  Add the same five with the `MARKET_HTTP_` prefix to `market-http` (sampler arg default `0.01`) plus fixed `OTEL_PROPAGATORS: none`.

- [ ] **Step 2: ECS.** Add to `infrastructure/ecs/market-http.json`: `OTEL_SDK_DISABLED=true`, `OTEL_PROPAGATORS=none`, `OTEL_TRACES_SAMPLER=parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG=0.01`, `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod`.

- [ ] **Step 3: `.env.example`.** Document the ten host-side variables next to the existing `MARKET_STREAMER_*` / `MARKET_HTTP_*` entries, commented out, tracing disabled by default, with one line on what each maps to.

- [ ] **Step 4: Verify** `docker compose -f compose.local.yaml config --quiet`, `docker compose -f compose.prod.yaml --env-file .env.example config --quiet`, and `python -m json.tool infrastructure/ecs/market-http.json >/dev/null`. Expected: all succeed.

- [ ] **Step 5: Commit**

```bash
git add compose.local.yaml compose.prod.yaml infrastructure/ecs/market-http.json .env.example
git commit -m "chore: wire OpenTelemetry settings for market services"
```

### Task 5: Overhead measurement and documentation

**Files:**
- Create: `scripts/bench-publish-tracing.py`
- Create: `docs/operations/2026-10-22-price-stream-tracing.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: `market_streamer.stream.publish`; `KTB_TEST_VALKEY_URL`.

- [ ] **Step 1: Write the benchmark.** `scripts/bench-publish-tracing.py MODE` with `MODE` in `off | sampled-out | sampled-in`. It installs a `TracerProvider` with `SimpleSpanProcessor(InMemorySpanExporter())` and sampler `ALWAYS_OFF`/`ALWAYS_ON` (no provider for `off`), builds 26 distinct prices (one batch, codes `000001`…`000026`, volume increasing every round), warms up 50 publishes, then times 1000 `publish()` calls against `KTB_TEST_VALKEY_URL` (flushdb first) and prints `mode`, mean and p95 ms per call, and max RSS (`resource.getrusage`). One process per mode.

- [ ] **Step 2: Run** all three modes:

```bash
for m in off sampled-out sampled-in; do KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run python scripts/bench-publish-tracing.py $m; done
```

Expected: three result lines; record them.

- [ ] **Step 3: Write `docs/operations/2026-10-22-price-stream-tracing.md`:**
  - the trace shape (`publish` → `deliver` per client)
  - T0/T1/T2 definitions and caveats: `publish_to_receive_ms` needs cross-host clock sync; T0 is before the pipeline executes, not Valkey's `PUBLISH`; T2 is `send()` returning, not network delivery; `deliver` includes waiting in FastAPI's SSE buffers
  - sampling: the streamer's ratio drives market-http event spans; REST is sampled locally; TraceQL results are samples
  - environment variables: the full table of host-side and container-side `OTEL_*` names, their defaults, and what the AWS test system must set for both services
  - failure behavior: Tempo down → spans dropped, one ERROR log line per failed export batch (repeats every few seconds while Tempo stays down; exclude logger `opentelemetry.exporter` from ERROR alerts), exit delayed at most ~2 s; misconfigured `OTEL_*` values log a WARNING and fall back; `OTEL_SDK_DISABLED` must be exactly `true`
  - log correlation: `trace_id`/`span_id` appear on log lines written inside a span; the Grafana log→trace link is configured outside this repo
  - the Step 2 benchmark results, including the ~85-byte growth of sampled price messages
  - the package note: issue #368 named `ktb_core/telemetry.py`; tracing lives in `ktb_telemetry` to keep core dependency-free

- [ ] **Step 4: Update `AGENTS.md`.**
  - Add `packages/telemetry` (`ktb_telemetry`) to the Repository map table: "OpenTelemetry tracing setup and log trace ids; keeps OpenTelemetry dependencies outside core".
  - Add a convention bullet: logs record operational events and spans record durations. Wrap a service's main work in `ktb_telemetry.tracing.tracing(name)` after `setup_logging()` (sync: around the work; async: around `asyncio.run`). Use `trace.get_tracer(__name__)` directly. Never keep one span open for a whole long-running process or connection.
  - Add a deprecation note: `start_logging`'s `elapsed_ms` is deprecated and gets removed once every `start_logging` service has an equivalent span. Other ad-hoc timers (`market-collector/store.py`, portfolio-builder `Stopwatch`, `infrastructure/questdb/inspect_checkpoint.py`) are candidates to move to spans.

- [ ] **Step 5: Verify** `uv run ruff check . && uv run ruff format --check . && uv run tach check && KTB_TEST_VALKEY_URL=redis://localhost:6379/0 uv run pytest`. Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/bench-publish-tracing.py docs/operations/2026-10-22-price-stream-tracing.md AGENTS.md
git commit -m "docs: document price stream tracing and publish overhead"
```
