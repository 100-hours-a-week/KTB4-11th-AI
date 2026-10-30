# Disclosure inference on Modal

CPU services call authenticated HTTPS endpoints. Modal serves two independently scaled
models on 24GB GPUs (L4, with A10 as an availability fallback):
Context-9B with score10 LoRA for documents/queries, and Qwen3-Reranker-4B with the verified
last-7d43c4 adapter. Retrieval returns evidence; it never generates an answer.

## Runtime and snapshots

`app.py` enables `enable_memory_snapshot=True` and
`experimental_options={"enable_gpu_snapshot": True}`. Both classes load the model and run
a small forward pass in `@modal.enter(snap=True)`. Each model has zero minimum and buffer
containers, accepts one input at a time, and is capped at two containers. After five idle
seconds, its container becomes eligible to scale down. Scale-down is managed by Modal and
is not an immediate shutdown command after an HTTP response. GPU memory
snapshots remain an alpha feature; a restore hit is not guaranteed.

At most four GPU containers can exist across the two models: two embedding workers and
two reranking workers. The SQS publisher's concurrency of four is a separate setting;
it does not configure or validate
GPU concurrency. Four workers are a capacity limit, not four permanently allocated GPUs.

## Server polling and hourly inference batches

The existing CPU server owns backend polling at 09:00, 10:00, 11:00, 12:00, 13:00,
14:00 and 15:00 KST on operating days. Modal has no backend poller or hourly cron.
A poll is not an inference request: an empty poll must not call Modal, including its
GPU-backed identity endpoints. Financial facts and identified sections are read from
PostgreSQL without starting either model.

The existing OpenDART API publisher collects short and long filings in one intake.
News-preprocessor fetches and parses each long filing in `OpenDart.article`, then
standard scraping stores its type, documents, sections and accounts in one transaction. Supported periodic financial
accounts and named sections remain available through SQL tools. The indexer reads the
database to embed narrative passages; it never fetches OpenDART or source archives.

When a poll yields narrative retrieval work, the server groups available queries for
that hourly batch, obtains query vectors, reads candidates from pgvector, and submits
the candidate lists for reranking. Complete the query-embedding stage together, then
complete the reranking stage together. Each model can scale down after its own last
call; do not keep the embedding GPU waiting for all reranking or answer generation.
Do not deliberately leave gaps longer than five seconds within a model's batch, because
a later call can start another container and repeat initialization. Grouping here means
server-side scheduling; it does not raise the measured per-model input-token budget.

The retrieval library runs inside portfolio-builder and avoids
inference for missing reports and empty narrative indexes. The existing hourly
`portfolio-rebalancer` polling endpoint returns accounts, not pending RAG queries.
Connecting an hourly search-request batch therefore requires a backend request contract
and a CPU caller; that integration is not implemented by the Modal scale settings.
No account polling or order publication is changed by this deployment.

At seven nonempty hourly batches per day, startup counts are 154 for 22 operating days
or 210 for 30 days. Empty polls reduce these counts. One batch can contain several
searches, so startup count and inference count are separate cost inputs. Retrieval
costs exclude the independent indexing job and downstream answer generation.

## Measured parallel inference

On 2026-10-29, the deployed v7 models processed identical fourteen-call batches with
client concurrency one and two. Each reranking call had fifty synthetic candidates,
1,016 characters per candidate. Provider execution histories confirmed peak overlap one
and two respectively. Settings were applied with the autoscaler API; weights and images
were not replaced.

| Model and initial state | Sequential seconds | Parallel seconds | Reduction |
| --- | ---: | ---: | ---: |
| Embedding, warm | 9.71 | 4.28 | 55.94% |
| Embedding, zero workers | 17.21 | 12.07 | 29.86% |
| Reranker, warm | 192.32 | 102.06 | 46.93% |
| Reranker, zero workers | 201.94 | 122.75 | 39.22% |

Each row is one batch, not a percentile or production SLA. Timings include SDK request,
queueing, inference and result transfer, excluding backend polling, pgvector search,
answer generation and container shutdown. Warm timings also exclude preparing the
second worker. More workers reduce latency by using additional GPUs; cost does not fall
by the same percentage. Final verification found zero running inputs, zero runners and
an empty container list. All minimum/buffer settings were restored to zero.

[Comparison and current hourly assumptions](https://docs.google.com/document/d/1cKUcbLhc1Sn4ez6XCdgt6y82BnTneBzy_a9JDEf8h9c/edit)
and [measurement archive](https://drive.google.com/file/d/1jgeZQow5ZiD-v93Ix4ynj2wU_7oSFjHl/view)
contain the inputs, model identities, raw observations and execution-history checks.

`weights.py` downloads pinned base and adapter revisions during the image build, verifies
the adapter hash and nonzero/finite LoRA tensors, and writes each model into its own image.
Runtime model loading uses local paths and offline mode. A snapshot miss still has to load
the model, but cannot re-download it from Hugging Face. Image downloads and deserialization
still take time; startup and inference measurements are recorded in the final implementation plan.

The model settings live in `packages/core/src/ktb_core/embedding/config.py`, alongside
the existing news configuration. Modal copies that file into its image as
`/root/model_config.py`; CPU services import the same disclosure identity values.
The separate disclosure model files were removed. External inference HTTP calls belong to the indexer and retrieval library; core contains no HTTP transport or GPU dependencies.

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

Apply collection migrations `0012`–`0014` from PR #441, then vector migration `0015`
from PR #432 through the existing dedicated migration job. Migration `0015` creates
`disclosure_narrative_chunks` with `halfvec(1024)` and keeps the legacy
`disclosure_chunks` with `halfvec(2000)`. Dimensions are checked against the parent index;
model revisions, adapter hash and transform are recorded with every new index. Activation
replaces only the same dimension, preserving the legacy index for rollback.

The existing hourly news-preprocessor service archives originals and stores parsed
documents, sections and supported financial accounts. Its `OnSuccess` and final
`OnFailure` start the DB-only indexer. There is no separate collector command, RSS path
or additional timer. A collection failure does not block indexing of documents already
stored in the DB. Only news-preprocessor uses the DART key and archive volume. News-http
and the weekday 06:00 portfolio pipeline keep their existing roles. No eligible
documents means zero disclosure inference calls. GPU serving PR #430 owns no migration.

Parsed sections retain whether they came from narrative or a table. Known narrative topics
are embedded; tables and unclassified sections remain available through direct section
reads. Financial facts remain structured rows for direct SQL tools. Context windows never cross
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
[final implementation plan](../../../docs/superpowers/plans/2026-10-29-disclosure-modal-integration.md)
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

Live validation is summarized in the final implementation plan. Raw measurements are maintained outside Git.
