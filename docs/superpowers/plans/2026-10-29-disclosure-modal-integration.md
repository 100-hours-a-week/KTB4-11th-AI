# Disclosure RAG: Final Implementation Plan

Finalized: 2026-10-29, after implementation and validation.
Scope: issue #346. This is the only implementation document included in this PR.
Intermediate designs, plans, handoffs and raw measurement files are maintained outside Git.

## 1. Service boundaries

| Component | Responsibility |
| --- | --- |
| disclosure-indexer | Collect periodic OpenDART filings, retain source archives, parse sections/facts and index narrative passages |
| disclosure-retrieval | Read financial facts and sections directly; return ranked narrative evidence without answer generation |
| Modal Embedder | Encode ordered document context groups and queries with the same pinned Context checkpoint |
| Modal Reranker | Score exact-search candidates with the trained Qwen model |
| portfolio-builder | Use disclosure evidence and technical analysis before saving a new portfolio |

The existing news pipeline keeps its own model and configuration. CPU services call Modal
through authenticated HTTPS and do not import Colab code, Modal SDK or GPU libraries.

## 2. Storage and incremental indexing

- [x] Keep source ZIP/XML archives outside the repository and retain receipt IDs, filing
  periods, correction families, source hashes, physical file names and parsed text offsets.
- [x] Recheck new/corrected/replaced sources and eligible retries without duplicating data.
  Bound attempts, distinguish collection/parsing/embedding failures and recover expired leases.
- [x] Store financial facts separately, preserving consolidated/separate scope, source strings
  and normalized NUMERIC values. Missing values remain null. Store named sections for direct reads.
- [x] Embed recognized narrative sections. Tables and unclassified sections remain accessible
  through section storage. Context groups preserve source order and never cross physical files.
- [x] Add migrations 0012–0016 after the existing dev head. Store new narrative vectors in
  halfvec(1024), preserving legacy halfvec(2000) data. Enforce parent-index dimensions and identity.
- [x] Activate indexes atomically. During rollback, accept an older parser's index only when
  its source hash matches the latest document. Never serve an index from a replaced source.

Run migrations through the existing dedicated migration job, then build the new index before
switching callers. To roll back, restore the previous provider/model/prefix and 2000 dimensions;
no schema downgrade is required. Schema downgrade refuses automatic deletion of 1024 indexes.

## 3. Model contracts and Modal deployment

The authoritative manifest is `packages/core/src/ktb_core/disclosure_models.json`, packaged
with core and copied into the Modal images. Core contains shared identity data and generic
HTTP handling; inference clients belong to their services.

| Role | Model and selected adapter |
| --- | --- |
| Embedding | perplexity-ai/pplx-embed-v2-context-9b-preview; score10 adapter SHA256 starts 1f5d4367 |
| Reranking | Qwen/Qwen3-Reranker-4B; verified last adapter SHA256 starts 7d43c402 |

- [x] Pin base and adapter revisions, verify hashes and finite/nonzero LoRA tensors, and
  refuse mismatched response identities, dimensions, chunk IDs or nonfinite outputs.
- [x] Use the checkpoint's document/query encoding paths and prefixes. Apply the evaluated
  INT8 output transform, retain the first 1024 dimensions and L2-normalize. Embedding weights
  remain bf16. Enforce 16,384 tokens using the checkpoint tokenizer without truncation.
- [x] Use the financial yes/no chat template, NF4 double quantization, float32 preparation,
  no KV cache and last-token logits for reranking.
- [x] Bake weights into separate Modal images and load them offline. Warm up inside
  `@modal.enter(snap=True)` with GPU memory snapshots enabled.
- [x] Use a 120-second idle window and at most two containers per model. Default GPU
  preference is L4,A10 after an observed L4 capacity shortage; both are 24GB options.
- [x] Complete Modal redeployment and authenticated live GPU checks. The validated deployment
  version is v7. Temporary validation credentials were revoked after measurement.

Deployment command from the repository root:

```bash
modal deploy infrastructure/inference/disclosure/app.py
```

GPU snapshots reduce repeated initialization but do not guarantee allocation availability or
a restore hit. The observed initial L4 wait must not be treated as model compute time.

## 4. Retrieval and builder integration

- [x] Apply company/report/date filters and compute exact cosine distance across every
  eligible chunk. Do not use HNSW or IVFFlat candidate pruning.
- [x] Select 50 candidates per latest/history scope, rerank and return five by default.
  Keep cosine, reranker score and logit margin separate; include source IDs, URL and offsets.
- [x] Return valid empty results without GPU calls when no searchable chunks exist. Keep
  upstream failure (503), input-budget rejection (422) and successful empty search distinct.
- [x] Require successful technical analysis and disclosure search before new portfolio
  submission. A valid empty search completes the tool call but does not prove risk is absent.
- [x] Follow same-origin HTTP 303 result URLs with GET without resubmitting inference.
  Budget redirect waits and apply socket timeouts. Arbitrary slow streaming body reads are
  not subject to hard wall-clock cancellation by the standard-library client.

## 5. Configuration and scheduling

Both Compose files pass environment-only model URLs and workspace proxy tokens. The Modal
endpoints are separate web functions rather than one OpenAI-compatible base URL. Legacy
OpenAI-compatible configuration remains available for the preserved 2000-dimensional path.

Set these values together for the Modal path:

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

- [x] Schedule indexing daily at 05:00 KST, aligned with hourly news collection and before
  the weekday 06:00 portfolio pipeline. news-http itself is a long-running HTTP service.
- [x] Run no inference when there is no eligible work; retain retries when no new filing appears.
- [x] Build separate CPU service images and include both services in the shared app image.
  Keep GPU dependencies only in Modal images.

## 6. Verification results

- [x] Merge current dev (0f61ead) and run the dedicated-DB suite: 1,327 passed, 49 skipped,
  two subtests passed. Ruff lint/format, module/dependency boundaries and whitespace checks passed.
- [x] Verify migration upgrade/downgrade, both CPU images, packaged model identity, absence
  of Modal/torch in CPU images and Compose propagation of model settings/timeouts.
- [x] Pass CI Dev, including Python 3.14 compatibility, requirements verification, dependency
  audit, all service images and the combined app image. Server deployment was skipped for the PR.
- [x] Validate document/query embedding and 50-candidate reranking on actual GPUs. A
  16,003-token document encoded in 8.634 seconds, with peak allocated/reserved VRAM of
  19.04/21.34 GiB; reported free memory was 0.42 GiB. Concurrency remains one input per container.
- [x] Recompute GPU query vectors and reranking for 34 answerable narrative calibration
  questions. Reuse verified full-report document vectors: 9,101 eligible chunks from five reports.

| Metric | Result |
| --- | ---: |
| Recall@5 | 1.0000 |
| nDCG@5 | 0.978290 |
| MRR | 0.970588 |
| Combined retrieval p50 | 14.540 seconds |
| Combined retrieval p95 | 19.656 seconds |

These scores validate compatibility with the original cached full-report corpus, not the new
production parser/classification/context grouping policy. The benchmark uses evaluation
metadata and preceding context; production reranking input differs. The DB measurements use
a local temporary test table. Labels have not completed SME review. No answer-generation F1
or RAGAS run is claimed. Raw datasets, training artifacts and measurement records stay outside Git.

## 7. Remaining checks before production cutover

- Evaluate the actual narrative classification, metadata and context groups built by the indexer.
- Freeze configuration/labels before separately evaluating the final 178 questions.
- Collect a larger snapshot/allocation sample and random 100-requests/day latency and billing data.
- Configure permanent deployment credentials, apply production migrations and deploy CPU services.

Official references: [GPU snapshots](https://modal.com/docs/guide/memory-snapshots),
[GPU preferences](https://modal.com/docs/guide/gpu),
[303 result URLs](https://modal.com/docs/guide/webhook-timeouts),
[proxy authentication](https://modal.com/docs/guide/webhook-proxy-auth).
