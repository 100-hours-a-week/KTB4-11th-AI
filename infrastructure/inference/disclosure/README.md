# Disclosure GPU serving and timing

This directory contains the existing pinned embedding/reranking runtime and its Modal
deployment. It is a standalone GPU project outside the CPU uv workspace. Model weights
are downloaded into the image at build time; CPU application images do not install GPU
dependencies. The model manifest remains packages/core/src/ktb_core/embedding/config.py.

Deploy with Modal SDK 1.3.5:

```bash
uv run --no-project --with modal==1.3.5 modal deploy infrastructure/inference/disclosure/app.py
```

DISCLOSURE_INFERENCE_APP defaults to disclosure-inference. Set a distinct name for
isolated verification, resolve its actual web URLs through the SDK, and stop the test
app afterward. Long app names can result in hashed endpoint suffixes. Do not replace
the shared deployment to force cold-start experiments. Deployment changes invalidate
snapshots. The model identity and embedding signature are unchanged by instrumentation.

Modal stdout emits JSON modal_model_loaded, modal_container_ready and modal_inference
events. HTTP responses add optional timing metadata with request_id, function_call_id,
container_id and initialization_id. CPU disclosure_timing events correlate these with
whole search, query/document embedding, vector search and rerank elapsed time.

model_load_ms includes model loading and warmup. inference_ms measures the server
handler. application_startup_ms measures fresh application initialization through the
post-snapshot hook; it excludes platform scheduling/boot. restore_hook_ms measures
only that hook, not the snapshot restore itself. A restored container has a new task
ID with the snapshot's initialization ID. The post-snapshot hook resets first-request
state and warm/restored requests never recount the historical model-loading duration.

Full platform cold_start_ms remains null without verified provider data. The optional
platform_timings.py collector is bounded and best effort. Modal FunctionCallList
returned UnimplementedError during live verification. This is an observed API
limitation, not a measured zero startup duration:

```bash
uv run --no-project --with modal==1.3.5 python infrastructure/inference/disclosure/platform_timings.py --app disclosure-inference --class-name Embedder --method query_embedding
```

See docs/operations/disclosure-timing.md for measurement definitions and live results.
CPU tests use fake models; GPU model dependencies are imported only when a model loads.
