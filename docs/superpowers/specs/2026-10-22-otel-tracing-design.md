# OpenTelemetry tracing for the price stream

**Date:** 2026-10-22
**Status:** Draft
**Issue:** #368 (Mandatory 1–3 for the price path, plus Bonus 1)

## Purpose

`ktb_core.logging.start_logging` records `run_start`/`run_end` with `elapsed_ms`, but logs cannot show nested timing or connect work across processes. We need to see where time goes between a price leaving `market-streamer` and the same event being handed to a client by `market-http`, and to keep watching it in production through Grafana and Tempo in our VPC.

This work adds a shared, service-independent tracing setup in a new workspace package, `ktb_telemetry`, separates timing (spans) from operational events (logs), and connects `market-streamer`'s publish to `market-http`'s SSE fan-out in one trace.

## Scope

In scope:

- `ktb_telemetry` (new package `packages/telemetry`, following the `ktb_aws` pattern): tracer provider setup and flush for any service, plus `trace_id`/`span_id` on log lines written inside a span.
- `start_logging`'s `elapsed_ms` marked deprecated in docs. `ktb_core` itself is not changed.
- `market-streamer`: one span per price publish, with trace context and the publish time (T0) carried in sampled price messages.
- `market-http`: standard FastAPI instrumentation for REST routes; per-event spans on `/prices/stream` that are children of the streamer's publish span, ending when ASGI `send()` returns (T2).
- Deployment wiring through standard `OTEL_*` environment variables only; first enabled on the separate AWS test system (see Deployment).
- A measurement of `publish()` overhead with tracing off, sampled out, and sampled in.
- Documentation: the logging/tracing split and how to add tracing to another service.

Not in scope:

- Message replay or load generation of any kind.
- The before/after comparison with #249, event-loop lag measurement, fan-out queue metrics.
- `/orderbooks/stream` and orderbook payloads.
- Tracing for other services; removing `elapsed_ms`; migrating other ad-hoc timers (`market-collector/store.py`, portfolio-builder's `Stopwatch`, `infrastructure/questdb/inspect_checkpoint.py`).
- Running Tempo. It lives in the existing PLG stack in the VPC, outside this repo, and is a precondition.

Anti-goals — the work fails even if spans appear when:

- Tracing changes the public REST or SSE payloads, or Pydantic models' schemas.
- Tempo being down, slow, or misconfigured affects publishing or streaming.
- A trace grows with connection lifetime (one trace per SSE connection lasting hours).
- Span count in `market-http` scales with messages × clients without a sampling bound.
- Logging and tracing become coupled again (e.g. `start_logging` opening spans).

## Shared tracing setup: `ktb_telemetry`

Issue #368 names `packages/core/src/ktb_core/telemetry.py`. `AGENTS.md` requires `ktb_core` to stay free of third-party dependencies, and `ktb_aws` already exists to keep AWS libraries out of core, so tracing lives in `packages/telemetry` (`ktb_telemetry`) instead (user decision, 2026-10-22). Only `market-streamer` and `market-http` depend on it; other services and their image requirements are untouched.

One public entry point: a context manager (proposed name `tracing(service_name)`) that a service wraps around its main work.

- On enter: build a `TracerProvider` with a resource whose `service.name` defaults to the given name, a `BatchSpanProcessor`, and an OTLP exporter, then set it as the global provider.
- On exit: flush and shut down the provider so pending spans are exported when the process ends.
- Opens no span of its own.

Configuration comes only from standard OpenTelemetry environment variables, so changing it never needs code changes: `OTEL_SDK_DISABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES` (for `deployment.environment`). No new pydantic settings.

Services use the OpenTelemetry API directly — `trace.get_tracer(__name__)`, `opentelemetry.propagate.inject/extract` — with no wrappers in `ktb_telemetry`. Domain spans (Valkey publish, SSE events) live in the services. `ktb_telemetry` must not grow helpers such as `publish_span()` or `sse_span()` (user decision):

```python
# market_streamer/__main__.py
with tracing("market-streamer"):
    asyncio.run(run(settings, datetime.now(UTC)))

# market_streamer/stream.py
tracer = trace.get_tracer(__name__)

async def publish(valkey, prices):
    with tracer.start_as_current_span("publish", kind=SpanKind.PRODUCER):
        ...
```

Failure behavior: export happens on the batch processor's background thread; when the endpoint is unreachable spans are dropped and the app continues. All `opentelemetry` loggers are raised to `ERROR` after setup so export retries and per-span "queue full" drops do not flood the JSON log stream. The export queue is the SDK's bounded `BatchSpanProcessor` queue (`OTEL_BSP_MAX_QUEUE_SIZE`, default 2048). The exporter timeout is fixed at 2 seconds in code, because the SDK default makes shutdown block for 6–10 s or more when Tempo is unreachable (measured: about 6.5 s against a refused port; with a 2 s timeout, 1.2 s refused and 2.1 s blackholed). With `OTEL_SDK_DISABLED=true` or no setup call, all spans are no-ops.

## Logging and tracing split

Rule: **logs record operational events** (run history, failures, state changes); **spans record durations**. New timing code uses spans, not `*_ms` log fields.

- `start_logging` stays pure logging: `run_start`, then `run_end` with outcome, exit code, and exception. It does not create spans.
- `run_end.elapsed_ms` stays for now and is marked deprecated in docs (`AGENTS.md` forbids new code comments). Eight services use `start_logging`, none of them traced yet, so it is still their only run timing. Remove it once every `start_logging` service has an equivalent span.
- `ktb_core.logging` is not modified. `tracing()` attaches a `logging.Filter` to every root handler present when it is entered (so services call `setup_logging()` first, as they already do) and removes it on exit. Inside a valid span the filter merges `trace_id` (32 hex) and `span_id` (16 hex) into the record's structured `fields`, which `JsonFormatter` already writes out. It works for `StructuredLogger` records, plain stdlib records (e.g. uvicorn), exceptions, and `start_logging` lines; lines outside a span are unchanged (verified in a spike against the real `ktb_core.logging`). This lets Grafana jump from a log line to its Tempo trace.
- A filter on the root logger would miss records from child loggers, and a log record factory would be overwritten by `extra={"fields": ...}`, so the filter goes on handlers.

## market-streamer: publish span and context in the payload

- `main()` wraps the existing run in the tracing context manager. There is **no run-level span**: the streamer runs for hours, and a current run span would make every publish part of one endless trace. Publish spans are roots.
- `publish()` (prices) runs inside one span, kind `PRODUCER`, covering the existing read-compare-write work for the batch. Useful attributes: number of input prices and accepted prices.
- If the span is sampled (`span.is_recording()`), each accepted price payload gets two extra fields:
  - `traceparent`: the W3C trace-context string from `propagate.inject`.
  - `published_at`: wall-clock time taken just before the write pipeline executes (T0), ISO 8601 with microseconds.
- Unsampled batches publish exactly today's payload.
- The existing `timestamp` stays the Kiwoom event time. `SET price:{code}` stores the same payload as `PUBLISH`, so stored values carry the fields too.

Compatibility: `TickPrice`/`Price` use Pydantic's default `extra="ignore"`, so REST and SSE output is unchanged. Tests must prove it for both the stored-key path (`current_prices`) and the stream path. Within this repo only `market-streamer` and `market-http` read or write `price:{code}`; `market-http`'s QuestDB fallback write simply lacks the new fields. Outside it, the only reader is KTB4-11th-BE's `AiQuoteAdapter` (GET and Pub/Sub on `price:{code}`), which parses with Jackson `readTree` and reads named fields via `path()`, so unknown fields are ignored (checked on its `dev` branch, 2026-10-22).

## market-http: REST instrumentation and SSE event spans

**REST.** Use FastAPI's built-in telemetry (FastAPI 0.142+) for REST server and operation spans instead of `opentelemetry-instrumentation-fastapi`. Pass `telemetry={"auto_configure": False, "exclude": is_stream}` to `FastAPI()`. `exclude` skips the `/stream` routes so a server span never lasts as long as a connection. `auto_configure=False` keeps `ktb_telemetry` the only exporter: on 0.142 FastAPI otherwise adds its own OTLP span, metric, and log exporters from `OTEL_EXPORTER_OTLP_ENDPOINT`, exporting every span twice (0.143 makes it opt-in). FastAPI's telemetry reads the global propagator, so the empty propagator set in `main()` also covers it.

**Per-event spans on `/prices/stream`.** For each Pub/Sub message:

- If it has no `traceparent`, no span is created (the streamer did not sample it).
- Otherwise start a span (kind `CONSUMER`) whose **parent is the streamer's publish context** extracted from `traceparent`. One trace is then "one publish → its fan-out to every connected client".
- T1 is the span start, right after `pubsub.listen()` yields the message. Attributes: `code`, T1−T0 from `published_at` (cross-host; depends on clock sync), and a per-connection id so one client's stream can be filtered.
- The span stays open through validation and serialization and ends when the event's ASGI `send()` returns (T2), so its duration is T2−T1 and includes time waiting in FastAPI's SSE buffers behind a slow client. If `published_at` and the end time are both on the span, T2−T0 is derivable.

**Ending at `send()` — the one piece of custom code.** FastAPI's SSE route runs the generator in a producer task connected to the response through two one-item buffers (FastAPI 0.142 `routing.py:579–665`), so resuming after `yield` is not the send. A small pure-ASGI middleware handles only stream paths:

- It keeps a per-request FIFO and exposes it through a context variable. The handler calls one hook, `track_event(raw)` for a live message or `track_event()` for a snapshot event, which appends the event's span or `None` (initial snapshot, untraced or rate-limited messages), so order always lines up. Span creation, propagation, and the rate limiter live in `market_http/observability/tracing.py`; the route handler only calls the hook. Likewise `market_streamer/observability/tracing.py` holds the publish span (a decorator on `publish()`) and the trace fields `publish()` merges into each message.
- The middleware wraps `send`; after `await send(message)` for an `http.response.body` chunk that is not a keepalive comment (starts with `:`), it pops one entry and ends it if not `None`.
- When the request finishes or the client disconnects, it ends any spans still queued.

Keep it short and readable; if the matching logic stops being obvious, prefer simplicity over precision at T2.

The initial subscribe and snapshot are not given a span in this work.

## Sampling

Producer-driven. `market-streamer` uses `OTEL_TRACES_SAMPLER=parentbased_traceidratio` with a ratio chosen per environment; it produces roughly 16–30 publish spans per second at today's KOSPI 200 rates, so a high ratio is affordable there. `market-http` event spans follow the producer's decision because they are children of it; their volume is (sampled publishes × prices per batch × clients). Raising or lowering the streamer's ratio is the main knob and needs no code change. Because fan-out multiplies that volume by the number of clients, `market-http` also caps `deliver` spans with a per-process sliding-window limiter (`MARKET_HTTP_TRACE_SPANS_PER_SECOND`, default 100); a message over the limit streams as if untraced. Unfinished spans need no separate cap: SSE backpressure holds a slow client's generator, so each connection holds only a few. Tempo/TraceQL results are samples, not exact percentiles or loss rates.

## Overhead check

A test or small script times `publish()` on a synthetic batch of about 26 prices against Valkey (the existing Valkey test setup) in three modes: tracing disabled, sampled out, sampled in. Record results in the operations doc. Expected: span work is microseconds per batch against millisecond Valkey round trips; the larger effect is about 85 extra bytes per sampled price message.

## Deployment

Where it runs: `market-streamer` does not run in production (`.github/workflows/cd.yaml`). The first target is a separate AWS test system with its own Valkey, ECS cluster, ALB, `market-http`, and `market-streamer`; its definitions are not in this repo. The code therefore stays environment-agnostic: tracing is enabled per environment purely through environment variables set by whoever deploys that environment, and `deployment.environment` (via `OTEL_RESOURCE_ATTRIBUTES`) tells environments apart in Tempo.

- Every environment that enables tracing sets, for both services: `OTEL_EXPORTER_OTLP_ENDPOINT` (Tempo in that VPC), `OTEL_TRACES_SAMPLER`/`OTEL_TRACES_SAMPLER_ARG`, `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=<env>`; and for `market-http`, `OTEL_PROPAGATORS=none` (see below).
- Production `market-http` (ECS) takes its environment from the `MARKET_HTTP_ENV_JSON` GitHub Actions variable (since `233ad78`). `tracing()` does nothing unless an OTLP endpoint is set, so production stays untraced until that variable gains the `OTEL_*` entries (user decision, 2026-10-22).
- `compose.local.yaml`/`compose.prod.yaml`: pass the `OTEL_*` variables through for `market-streamer` and `market-http`, defaulting to disabled.
- `.env.example`: document the variables with tracing disabled by default.
- The tracing runbook in `README.md` lists the variables the AWS test system must set.

Inbound trace context on `market-http`: the API is public with CORS `*`, so client-supplied `traceparent` headers must not force sampling or join foreign traces. `market-http` `main()` sets an empty global propagator in code (`OTEL_PROPAGATORS=none` is kept as belt-and-braces) so the FastAPI instrumentation ignores incoming headers, and REST requests are sampled locally by their own ratio. The SSE code extracts the Valkey message's context with `TraceContextTextMapPropagator` directly, independent of the global propagator. (Builder's call.)
- Dependencies: `ktb-telemetry` depends on `opentelemetry-api`, `opentelemetry-sdk`, and `opentelemetry-exporter-otlp-proto-http`. `market-streamer` and `market-http` add the `ktb-telemetry` workspace dependency (plus `opentelemetry-api` for their own direct imports), with matching `COPY`/install entries in their Dockerfiles and `docker/app.Dockerfile`, `tach.toml` modules, and regenerated `docker/requirements/market-streamer.txt`, `market-http.txt`, and `app.txt`.

## Testing

- `ktb_telemetry`: setup with an in-memory exporter; disabled mode yields no-op spans; an unreachable endpoint neither raises nor delays exit beyond the bounded timeout; the filter adds `trace_id`/`span_id` through the real `setup_logging` pipeline only inside a valid span, keeps existing structured fields, and is removed on exit.
- `market-streamer`: sampled publish adds `traceparent` and `published_at`; unsampled publish payload is byte-identical to today's; publish span attributes.
- `market-http`: stream events with `traceparent` produce a child span of the given context ending after send; events without it produce none; keepalives do not consume queue entries; disconnect ends queued spans; public SSE and REST payloads are unchanged with the extra fields present.
- No test needs a running Tempo.

## Documentation

- `README.md`: how to read the price-stream trace in Grafana/Tempo, what T0/T1/T2 mean and their caveats (cross-host clock sync for T1−T0; T0 is before the pipeline executes, not when Valkey runs `PUBLISH`; T2 is `send()` returning, not network delivery), sampling guidance, and the overhead results.
- `AGENTS.md`: the logging/tracing rule, the `elapsed_ms` deprecation, and steps to add tracing to another service (sync and async).

## Decisions

Decided by the user:

- Implement Mandatory 1–3 for the price path plus Bonus 1 on this branch; replay and the #249 comparison are out.
- Use the standard FastAPI instrumentation; a small custom middleware is acceptable for the true send boundary (option B), but simplicity wins over T2 precision.
- Tracing runs as ongoing monitoring, exporting to Tempo in the VPC. The cross-service price trace targets a separate AWS test system with its own Valkey, ECS, ALB, `market-http`, and `market-streamer`.
- Logging and tracing are separate; `elapsed_ms` is deprecated, not removed, because it is used beyond `market-http`.
- `ktb_telemetry` contains only OpenTelemetry SDK setup (`tracing(service_name)`) and the log filter it installs; no span helpers.
- Tracing lives in a new `ktb_telemetry` package, not `ktb_core`, to keep core free of third-party dependencies; `ktb_core.logging` is unchanged.

Builder's calls (made during design, open to review):

- Event spans are children of the publish span, not linked to it.
- `traceparent` and `published_at` only on sampled payloads.
- No run-level span in `market-streamer`; no span for the SSE snapshot.
- Deprecation by comment and docs, not a runtime warning.
- `market-http` ignores inbound trace headers (empty global propagator set in `main()`); REST sampled locally.
- Production `market-http` gets the variables with tracing disabled.
- `trace_id`/`span_id` are written to logs; wiring Grafana's log→trace jump (CloudWatch or Loki datasource) is outside this repo.
- Field names `traceparent` and `published_at`; context-manager name and exact span names.

## Left to the builder

- Exact span names and attribute keys (follow OTel messaging conventions where they fit).
- OTLP over HTTP or gRPC (the plan uses HTTP/protobuf).
- How the connection id is generated.
- Verified: a `service.name` passed in code wins over `OTEL_SERVICE_NAME`; `deployment.environment` from `OTEL_RESOURCE_ATTRIBUTES` still applies. Document this.
