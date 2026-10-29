# Disclosure inference on Modal

CPU services call authenticated HTTPS endpoints. Modal runs two independent 24GB GPU containers (L4, with A10 as an availability fallback):
Context-9B with score10 LoRA for documents/queries, and Qwen3-Reranker-4B with the verified
last-7d43c4 adapter. Retrieval returns evidence; it never generates an answer.

## Runtime and snapshots

`app.py` enables `enable_memory_snapshot=True` and
`experimental_options={"enable_gpu_snapshot": True}`. Both classes load the model and run
a small forward pass in `@modal.enter(snap=True)`. Containers scale to zero after 120 idle
seconds, handle one input at a time, and are capped at two containers per model. GPU memory
snapshots remain an alpha feature; a restore hit is not guaranteed.

`weights.py` downloads pinned base and adapter revisions during the image build, verifies
the adapter hash and nonzero/finite LoRA tensors, and writes each model into its own image.
Runtime model loading uses local paths and offline mode. A snapshot miss still has to load
the model, but cannot re-download it from Hugging Face. Image downloads and deserialization
still take time; startup and inference measurements are recorded in the October 29 validation report.

The immutable manifest is `packages/core/src/ktb_core/disclosure_models.json`. The runtime
copies that file into its image; both CPU services read the same file from the core wheel.
Core contains only identity data/functions, with no network client or GPU dependencies.

The reranker uses the evaluated financial chat template, NF4 with double quantization,
float32 preparation of non-quantized layers, no KV cache and last-token logits only. Its
prompt identity is now `qwen3-reranker-financial-chat-v2`. Existing deployed endpoints with
the older prompt identity must be redeployed; the clients intentionally reject them.

## Deploy separately from Docker

Run from the repository root after reviewing the deployment and expected costs:

```bash
python3 -m venv /tmp/disclosure-modal
/tmp/disclosure-modal/bin/pip install modal==1.6.3
/tmp/disclosure-modal/bin/modal setup
/tmp/disclosure-modal/bin/modal deploy infrastructure/inference/disclosure/app.py
```

`modal deploy` builds large model images and can create GPU snapshots. Deployment and actual GPU measurements were completed after the local integration;
see the validation report linked below. SDK 1.6.3 was used only to validate the app
definition locally. `DISCLOSURE_INFERENCE_GPU` accepts a comma-separated GPU preference list. The default is
`L4,A10`, following an observed L4 capacity shortage during validation.

Copy the three exact URLs printed by deployment: `Embedder.document_embeddings`,
`Embedder.query_embedding`, and `Reranker.rerank`. They are separate web functions, not
paths below one OpenAI-compatible base URL. All five HTTP endpoints, including identity,
require proxy authentication. Use a workspace proxy token, never the SDK account token.

## Docker configuration

Add these values to the deployment environment, keeping secrets out of Git:

```dotenv
DISCLOSURE_INFERENCE_BACKEND=modal
DISCLOSURE_EMBEDDING_DIMENSIONS=1024
DISCLOSURE_EMBEDDING_MAX_TOKENS=16384
DISCLOSURE_MODAL_DOCUMENT_URL=https://<document-endpoint>.modal.run
DISCLOSURE_MODAL_QUERY_URL=https://<query-endpoint>.modal.run
DISCLOSURE_MODAL_RERANK_URL=https://<rerank-endpoint>.modal.run
DISCLOSURE_MODAL_KEY=<workspace-proxy-token-id>
DISCLOSURE_MODAL_SECRET=<workspace-proxy-token-secret>
DISCLOSURE_INDEXER_TIMEOUT=150
DISCLOSURE_INDEXER_LEASE_SECONDS=600
DISCLOSURE_RETRIEVAL_TIMEOUT=150
PORTFOLIO_BUILDER_DISCLOSURE_RETRIEVAL_TIMEOUT=480
```

Both Compose files pass the settings into the appropriate services. They do not install
the Modal SDK, torch, PEFT, Transformers or bitsandbytes. Normal outbound HTTPS is enough.
Document/query prefixes are applied by the checkpoint; the old
`DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION` is ignored in Modal mode. The old model/base URI
settings remain only for the legacy 2000-dimensional provider.

A retrieval request may use one query embedding and two reranking calls when history is
included, so its caller allows 480 seconds versus 150 seconds per upstream call. 303 result polling uses a shared deadline without resubmitting the inference request.
Socket I/O timeouts also apply, with no automatic inference retries. Blocking streaming
body reads are not a general hard wall-clock cancellation guarantee. A slow snapshot miss can still return 503; the
historical restore measurements do not establish an availability guarantee.

## Storage, scheduling and cutover

Run migration `0016` through the existing dedicated migration job. It creates
`disclosure_narrative_chunks` with `halfvec(1024)` and keeps the legacy
`disclosure_chunks` with `halfvec(2000)`. Dimensions are checked against the parent index;
model revisions, adapter hash and transform are recorded with every new index. Activation
replaces only the same dimension, preserving the legacy index for rollback.

The new systemd timer checks filings daily at **05:00 KST**, aligned with one hourly news
collection run and before the weekday 06:00 portfolio pipeline. `news-http` itself is an
HTTP server, not a scheduled job. The timer does not run the indexer a second time in the
morning pipeline. It uses the company universe already stored by market-syncer. Source
refresh, late corrections and bounded embedding retries remain eligible even without new
filings. No eligible documents means zero inference calls.

Parsed sections retain whether they came from narrative or a table. Known narrative topics
are embedded; tables and unclassified sections remain available through direct section
reads. Financial facts keep their existing structured endpoint. Context windows never cross
physical files inside a ZIP and retain source order. A conservative 12,000-byte group budget
is followed by the checkpoint's exact tokenizer check at 16,384 tokens, with no truncation.
This grouping/classification is a serving change and needs its own quality evaluation.

Index documents before enabling Modal retrieval for callers. Exact cosine search scores
all chunks in the requested scope, selects 50, reranks and returns five by default. Latest
and historical scopes stay separate. Cosine, reranker score, margin and source URL are
returned separately; scores are not probabilities of a correct answer. Direct reads and
searches without any indexed narrative chunks never call Modal.

To roll back, restore `DISCLOSURE_INFERENCE_BACKEND=openai`, dimensions 2000, the previous
model/base URI and its query prefix. No schema downgrade is required. A schema downgrade
refuses to delete 1024 indexes automatically; removing them is a separate destructive step.

## Verification and remaining measurements

```bash
./scripts/run-tests.sh infrastructure/inference services/disclosure-indexer services/disclosure-retrieval
```

Local tests cover transport identities, proxy authentication, invalid vectors/scores,
physical-file boundaries, zero-GPU empty reads, DB activation and Top-50-to-Top-5 behavior.
The core wheel includes the manifest. See
[`2026-10-29-disclosure-modal-integration.md`](../../../docs/operations/2026-10-29-disclosure-modal-integration.md)
for execution results and limits.

Before production cutover, measure snapshot hit/miss startup at a larger sample size,
maximum-token VRAM, identical-input parity, halfvec Top-5 effects, quality on the frozen
evaluation set, end-to-end latency and cost at 100 randomly timed requests/day. Historical
scores and startup numbers do not validate this implementation.

## Official references

- [Memory snapshots, GPU snapshot flags, lifecycle warmup and limitations](https://modal.com/docs/guide/memory-snapshots)
- [Web functions, deployment and separate HTTPS URLs](https://modal.com/docs/guide/webhooks)
- [Proxy token authentication headers](https://modal.com/docs/guide/webhook-proxy-auth)
- [Model storage: Volume and image tradeoffs](https://modal.com/docs/guide/model-weights)
- [Modal App API](https://modal.com/docs/reference/modal.App)

Live validation: [October 29 GPU results](../../../docs/operations/2026-10-29-disclosure-modal-gpu-validation.md).
