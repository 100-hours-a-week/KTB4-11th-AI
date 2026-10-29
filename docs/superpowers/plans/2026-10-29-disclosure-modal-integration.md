# Disclosure RAG: Final Implementation Plan

Finalized: 2026-10-29, after implementation and validation.
Scope: service issue #346 / PR #432 and Modal serving issue #431 / PR #430.
Review and merge #432 before #430. This is the only implementation plan included in the PRs.
Intermediate designs, plans, handoffs and raw measurement files are maintained outside Git.

## 1. Service boundaries

| Component | Responsibility |
| --- | --- |
| news-preprocessor-disclosures | Collect periodic OpenDART filings, archive originals and store parsed documents, sections and financial facts in PostgreSQL |
| disclosure-indexer | Read stored documents from PostgreSQL, embed narrative passages and store vectors in pgvector |
| disclosure-retrieval | Read financial facts and sections directly; return ranked narrative evidence without answer generation |
| Modal Embedder | Encode ordered document context groups and queries with the same pinned Context checkpoint |
| Modal Reranker | Score exact-search candidates with the trained Qwen model |
| portfolio-builder | Use disclosure evidence and technical analysis before saving a new portfolio |

The hourly news command and read-only news-http server retain their existing roles.
The separate daily collection command belongs to the existing news-preprocessor package.
Data flows from OpenDART through that command into PostgreSQL, then through the DB-only
indexer into pgvector. The indexer does not call OpenDART or open source archive files.

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
- [x] Create the final disclosure schema in migration 0012 after revision 0011.
  The five unreleased revisions were consolidated after confirming no shared DB had applied them. Store new narrative vectors in
  halfvec(1024), preserving legacy halfvec(2000) data. Enforce parent-index dimensions and identity.
- [x] Activate indexes atomically. During rollback, accept an older parser's index only when
  its source hash matches the latest document. Never serve an index from a replaced source.

Run migrations through the existing dedicated migration job, then build the new index before
switching callers. To roll back, restore the previous provider/model/prefix and 2000 dimensions;
no schema downgrade is required. Downgrading 0012 to 0011 removes the disclosure tables,
including their stored documents and vectors; provider rollback does not require this operation.

## 3. Model contracts and Modal deployment

The model configuration is `packages/core/src/ktb_core/embedding/config.py`. It contains
the existing news settings and the pinned disclosure model configuration. Modal copies
this same Python file into its images as `/root/model_config.py`; CPU services import
its identity functions. Separate disclosure model JSON/Python files are unnecessary.
HTTP request and 303 result polling code stays inside each service's inference client;
core has no HTTP transport or GPU dependencies.

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

- [x] The 05:00 KST indexer timer starts `ktb-disclosure-collector.service` first.
  That unit runs `news-preprocessor-disclosures` using the existing news-preprocessor
  image, then the indexer reads PostgreSQL. `Wants` plus `After` preserves this order
  while allowing indexing of previously stored documents after collection failure.
  The hourly news command and weekday 06:00 portfolio pipeline retain their schedules.
- [x] Pass DART credentials through `NEWS_PREPROCESSOR_DART_API_KEY`; configure collection
  with `NEWS_PREPROCESSOR_DISCLOSURE_*`. Mount archives only in news-preprocessor.
  The indexer needs neither DART credentials nor filesystem access to originals.
- [x] Run no inference when there is no eligible work; retain retries when no new filing appears.
- [x] Build separate CPU service images and include both services in the shared app image.
  Keep GPU dependencies only in Modal images.

## 6. Verification results

- [x] Merge current dev (0f61ead) and run the dedicated-DB suite after ownership,
  migration and model configuration changes: **1,331 passed, 49 skipped, two subtests passed**
  in 44.61 seconds. The service-only branch passed 1,301 tests with 49 skips.
  Ruff lint/format, module/dependency boundaries and whitespace checks passed.
- [x] Compare all eight disclosure tables before and after migration consolidation:
  columns, checks, unique constraints, foreign keys and indexes match. Downgrade to 0011
  removes the feature tables; upgrading again recreates the same final schema.
- [x] Verify migration upgrade/downgrade, both CPU images, packaged model identity, absence
  of Modal/torch in CPU images and Compose propagation of model settings/timeouts.
- [x] The earlier implementation passed CI Dev, including Python 3.14 compatibility,
  requirements verification, dependency audit and image builds. The updated PR commits
  must complete their own CI checks before merge. PR CI does not deploy the CPU services.
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

## 8. Local execution and configuration changes

Migrate with `KTB_POSTGRES_DSN` through the dedicated migration job, not service startup.
After market-syncer has stored the corporation universe, run collection and indexing:

```bash
docker compose -f compose.local.yaml --profile jobs run --rm news-preprocessor news-preprocessor-disclosures
docker compose -f compose.local.yaml --profile jobs run --rm disclosure-indexer
```

The first command stores source documents, sections and structured financial facts without
calling an embedding model. The second reads stored documents and calls the configured
embedding endpoint only when eligible work exists. Retrieval returns evidence without
generating an answer. SQL financial and section reads do not require GPU inference.

Existing deployment files must move any `DISCLOSURE_INDEXER_DART_API_KEY` value to
`NEWS_PREPROCESSOR_DART_API_KEY` and rename collection options from
`DISCLOSURE_INDEXER_*` to `NEWS_PREPROCESSOR_DISCLOSURE_*`. Keep indexing options such as
chunk size, batch size, embedding timeout and lease duration under `DISCLOSURE_INDEXER_*`.
The archive volume stays the same; its mount moves to news-preprocessor.
