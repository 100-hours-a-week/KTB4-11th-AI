# Disclosure RAG: Final Implementation Plan

Finalized: 2026-10-29; PR ownership and migration split updated 2026-10-30.
Scope: GPU serving issue #431 / PR #430, following collection #440 / #441 and services #346 / #432.
Review and merge #441, then #432, then #430. Each PR contains its own final plan.
Intermediate designs, plans, handoffs and raw measurement files are maintained outside Git.

The 2026-10-30 direct-library integration was verified with **1,397 passed, 49 skipped, 2 subtests passed**
on the combined serving branch using a disposable PostgreSQL database. This run checks
CPU code and model contracts; it does not redeploy GPU workers or produce new inference timings.

## 1. Service boundaries

| Component | Responsibility |
| --- | --- |
| news-preprocessor | Collect short and long filings through the existing API publisher; archive originals and store parsed documents, sections and supported financial facts |
| disclosure-indexer | Read stored documents from PostgreSQL, embed narrative passages and store vectors in pgvector |
| disclosure-retrieval library (inside portfolio-builder) | Read financial facts and sections directly; return ranked narrative evidence without answer generation |
| Modal Embedder | Encode ordered document context groups and queries with the same pinned Context checkpoint |
| Modal Reranker | Score exact-search candidates with the trained Qwen model |
| portfolio-builder | Use disclosure evidence and technical analysis before saving a new portfolio |

The existing hourly news command uses `opendart/opendart.py` as its single API intake.
Long filings retain official `detail_type` codes and are parsed after scraping during
that same run. There is no RSS path, separate collector command/timer or historical
window loop. The DB-only indexer embeds eligible stored narrative content. It never
calls OpenDART or opens source archives. News-http remains read-only.

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
- [x] Keep the three collection migrations in PR #441: 0012 filing metadata/originals,
  0013 sections, and 0014 financial facts plus independent financial completion state.
  PR #432 adds vector migration 0015. GPU serving owns no schema revision. Store narrative vectors in
  halfvec(1024), preserving legacy halfvec(2000) data. Enforce parent-index dimensions and identity.
- [x] Activate indexes atomically. During rollback, accept an older parser's index only when
  its source hash matches the latest document. Never serve an index from a replaced source.

Run migrations through the existing dedicated migration job, then build the new index before
switching callers. To roll back, restore the previous provider/model/prefix and 2000 dimensions;
no schema downgrade is required. Downgrading 0015 to 0014 removes vector indexes and chunks
while retaining collected sources. Downgrading the collection revisions to 0011 removes
stored documents and financial facts; provider rollback does not require either operation.

## 3. Model contracts and Modal deployment

The model configuration is `packages/core/src/ktb_core/embedding/config.py`. It contains
the existing news settings and the pinned disclosure model configuration. Modal copies
this same Python file into its images as `/root/model_config.py`; CPU services import
its identity functions. Separate disclosure model JSON/Python files are unnecessary.
External inference HTTP requests and 303 result polling stay in the indexer and retrieval library;
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
- [x] Use a five-second idle window, zero minimum/buffer containers and at most two
  containers per model: two embedding plus two reranking workers, four in total. Default GPU
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

Retrieval is a Python workspace library under `packages/disclosure-retrieval`. Builder
tools pass their existing database engine directly. There is no retrieval FastAPI app,
router, HTTP client, port or container. Only external model inference uses HTTPS.

- [x] Apply company/report/date filters and compute exact cosine distance across every
  eligible chunk. Do not use HNSW or IVFFlat candidate pruning.
- [x] Select 50 candidates per latest/history scope, rerank and return five by default.
  Keep cosine, reranker score and logit margin separate; include source IDs, URL and offsets.
- [x] Return valid empty results without GPU calls when no searchable chunks exist. Keep
  model failures, input-budget rejections and successful empty searches distinct through tool errors.
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
```

- [x] The existing hourly news-preprocessor service starts the DB-only indexer through
  `OnSuccess` or final `OnFailure`. The indexer never starts collection. No separate
  collection command, collector timer or indexing timer remains. Previously stored
  documents can still be indexed after collection failure. News and the weekday
  06:00 portfolio pipeline retain their schedules.
- [x] Pass DART credentials through `NEWS_PREPROCESSOR_DART_API_KEY`; configure collection
  with `NEWS_PREPROCESSOR_DISCLOSURE_*`. Mount archives only in news-preprocessor.
  The indexer needs neither DART credentials nor filesystem access to originals.
- [x] Run no inference when there is no eligible work; retain retries when no new filing appears.
- [x] Build indexer and portfolio-builder CPU images; install the retrieval library in builder and the shared app image.
  Keep GPU dependencies only in Modal images.

## 6. Verification results

- [x] Unified API intake, typed retrieval and model-contract full suite on the disposable
  `ktb_typed_disclosure_services_test` database: **1,397 passed, 49 skipped, two subtests passed**.
  The collection branch passed 1,229 tests; the indexing/tools branch passed 1,367.
  These CPU checks do not deploy GPU workers or recompute model quality scores.
- [x] Upgrade fresh test databases through 0014 and 0015. Offline migration tests verify
  object ownership and reversed downgrade operations. Type metadata and nullable fiscal
  periods support nonperiodic filings; no collection-window table remains.
- [x] Build news-preprocessor, portfolio-builder and shared application images, then import
  them with networking disabled. Verify the RSS module, separate collector command and
  retrieval HTTP server are absent. Compose passes the same model settings to indexer
  and builder. Ruff lint/format, dependency boundaries and whitespace checks passed.
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
docker compose -f compose.local.yaml run --rm news-preprocessor
docker compose -f compose.local.yaml --profile jobs run --rm disclosure-indexer
```

The first command stores long-disclosure source documents, sections and supported
structured accounts without embedding those disclosures. Existing news article embedding
is unchanged. The second reads stored documents and calls the configured
embedding endpoint only when eligible work exists. Retrieval returns evidence without
generating an answer. SQL financial and section reads do not require GPU inference.

Configure `NEWS_PREPROCESSOR_DART_API_KEY` and the `NEWS_PREPROCESSOR_DISCLOSURE_*`
parsing/archive limits on news-preprocessor. Keep indexing options such as
chunk size, batch size, embedding timeout and lease duration under `DISCLOSURE_INDEXER_*`.
Mount the archive volume only on news-preprocessor.

## 9. Final hourly-batch settings and parallel measurements

Apply two embedding workers plus two reranking workers as the app's combined maximum
of four. Both pools have zero minimum/buffer containers and a five-second scaledown
window. Each worker accepts one input at a time. This limit is independent of SQS
publisher concurrency. Four is a limit, not a permanent allocation.

The existing v7 deployment received these settings through Modal's supported autoscaler
API. An unnecessary image rebuild was interrupted before publication; model images and
weights were not replaced. Source settings now match the live overrides, so a future
deployment preserves the same policy. A five-second idle setting is not an exact
observable container-termination deadline.

The CPU server owns 09:00 through 15:00 KST polling on operating days and should call
inference only for nonempty batches that need narrative retrieval. Modal has no backend
poller or hourly cron. The current hourly rebalancer polls account data; connecting a
backend batch of narrative queries requires its own request contract and CPU caller.
That integration is not implemented by these autoscaler changes. Existing account
polling, order publication and the morning/indexing schedules are unchanged.

The same query was submitted fourteen times per condition. Each reranking call received
fifty synthetic financial passages, 1,016 characters each. SDK submission, queueing,
inference and result transfer are timed; polling, pgvector, generation and post-result
shutdown are excluded. Warm cases exclude preparing the second worker. Zero-worker
cases confirm zero runners and running inputs before timing starts.

| Model and state | Sequential seconds | Parallel seconds | Reduction |
| --- | ---: | ---: | ---: |
| Embedding, warm | 9.71 | 4.28 | 55.94% |
| Embedding, zero workers | 17.21 | 12.07 | 29.86% |
| Reranker, warm | 192.32 | 102.06 | 46.93% |
| Reranker, zero workers | 201.94 | 122.75 | 39.22% |

These are eight single-batch observations, not percentiles or an end-to-end RAG benchmark.
All 112 timed calls matched provider history. Execution overlap was one for sequential
calls and two for parallel calls, with distinct parallel containers. Some containers
were replaced during a batch; this is a deployed-service comparison, not a fixed-card
microbenchmark. An initial pilot that included tracing queries in the timed loop was
excluded and retained only in raw evidence. Additional GPUs reduce latency without
reducing cost by the same percentage.

After the change, the targeted inference/client suite passed **37 tests**; Ruff lint,
formatting, SDK app-definition loading and live autoscaler settings checks passed.
Final verification found zero runners, zero running inputs and an empty container list.
Temporary benchmark minimums and caps were restored to zero minimum, two maximum,
zero buffer and five-second scaledown for both models. No accuracy scores were recomputed.

[Current comparison and hourly cost assumptions](https://docs.google.com/document/d/1cKUcbLhc1Sn4ez6XCdgt6y82BnTneBzy_a9JDEf8h9c/edit)
and [measurement inputs, raw timings and verification archive](https://drive.google.com/file/d/1jgeZQow5ZiD-v93Ix4ynj2wU_7oSFjHl/view).
