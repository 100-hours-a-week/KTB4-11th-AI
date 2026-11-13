# Disclosure timing logs

The shared inference transport emits `disclosure_timing` JSON events for
`query_embedding`, `document_embedding` and `rerank`. Retrieval also emits
`report_selection`, `vector_search` and whole `search` events. `duration_ms` measures
client elapsed time, including network and provider waiting. Events contain outcome,
search_id and per-inference request_id; exceptions remain unchanged and log only their type.
No query, document text, credentials or endpoint URL is added to these events.

Latest and historical reranking propagate the same search_id into their separate
threads. Their concurrent durations must not be added to derive total search time;
use the `search` event. Empty searches emit selection and total timing without GPU work.

The Modal serving branch adds optional authenticated response `timing` metadata.
The CPU caller accepts only matching request IDs, known fields, finite nonnegative
durations and bounded string identifiers. Old server responses continue to work and
produce null server fields.

| Field | Meaning |
| --- | --- |
| modal_inference_ms | Server handler execution, including request checks and inference |
| modal_model_load_ms | Model initialization/warmup for the first request of a fresh container; zero on warm/restored requests |
| modal_application_startup_ms | Fresh application initialization through the post-snapshot hook; excludes platform scheduling/boot; null for restores |
| modal_restore_hook_ms | Post-snapshot hook execution only, not snapshot restoration itself |
| modal_first_request | First measured HTTP inference on this container |
| modal_startup_kind | fresh, snapshot_restore, warm or unknown |
| modal_container_id / modal_initialization_id | Distinguish new containers and shared snapshot initialization |
| modal_function_call_id | Link the HTTP call to Modal logs/dashboard |
| modal_cold_start_ms | Full provider startup is unknown unless provider timing is available |

## Verification on 2026-11-13

Updated CPU modules ran only inside a temporary Python process in the existing dev
worker with read-only PostgreSQL connections. No worker source or environment files
were replaced. A separately named Modal test app used the existing pinned models.

| Stage, first search | Client elapsed | Server inference | Fresh model load |
| --- | ---: | ---: | ---: |
| Query embedding | 84.920 s | 0.444 s | 38.658 s |
| Vector search | 0.019 s | — | — |
| Rerank, 50 candidates | 50.818 s | 9.186 s | 33.180 s |
| Complete search | 135.818 s | — | — |

Both queries returned five actual disclosure passages. Second complete search took
100.215 seconds, including newly initialized containers; it was not a warm-only sample.
A document embedding call used a restored container with the same initialization ID
as the earlier embedding container: client 13.946 s, server inference 0.745 s, model
load 0. This demonstrates why startup and inference need separate logs.

The Modal SDK 1.3.5 protobuf exposes task_startup_time, but FunctionCallList returned
UnimplementedError in live verification. It cannot currently supply a verified full
platform cold-start duration. Do not substitute HTTP elapsed minus inference or the
post-restore hook duration. The optional collector on the serving branch logs
modal_platform_timing_unavailable with cold_start_ms=null on this failure.

These are individual validation observations, not p95, throughput or GPU-kernel-only
timings. Timing instrumentation does not change model identity or index signatures.
