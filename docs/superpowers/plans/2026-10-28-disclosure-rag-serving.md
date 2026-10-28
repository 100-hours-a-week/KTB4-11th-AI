# Disclosure Indexing and Retrieval Serving Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect existing disclosure services to Context score10/Qwen last, narrative RAG, and direct DB reads.

**Architecture:** CPU indexing manages collection, parsing, and incremental work, sending eligible documents to GPU inference. CPU retrieval reads facts/sections and selects vector candidates; GPU inference handles embeddings and reranking. Another service generates answers.

**Tech Stack:** Python 3.13 uv workspace, FastAPI, httpx, SQLAlchemy, PostgreSQL/pgvector, BeautifulSoup. Separate GPU image: Transformers, PEFT, bitsandbytes, FLA, causal-conv1d, matching evaluation versions.

**Spec:** [Disclosure serving design](../specs/2026-10-28-disclosure-rag-serving-design.md)

**Status:** Documentation complete; implementation unexecuted. Provider/HF IDs undecided. This plan does not automatically authorize paid deployment.

## Global Constraints

- Extend existing services; never execute Colab/Drive code.
- Context score10: 1024 dimensions; Qwen last: `7d43c4…`. Do not use best: `015b82…`.
- Same base/adapter revisions and transformations for documents/queries; evaluation input limit 16,384 tokens.
- Narrative vectors only; direct facts/sections use no GPU.
- Filtered exact cosine top-50, reranked default Top-5; no unvalidated threshold.
- Environment-only configuration/secrets; no keys in logs/files/commits. No inference clients/GPU dependencies in core.
- Preserve worktree/root uncommitted changes; this documentation task changes no service/deployment code.
- Add migrations after 0011; use a dedicated test DB such as `ktb_test`.
- Dependency changes require `uv lock` and service/app requirements exports using repository commands.

## Review Focus

1. Retry/correction/parser work survives days without new reports; no work means zero GPU calls (Task 3).
2. No cross-company/document context grouping or duplicated Qwen prefix (Tasks 1/3).
3. Never merge account names across periods, scopes, currencies, or units (Tasks 2/4).
4. Timeout/OOM/hash mismatch differs from successful empty results (Tasks 1/4).
5. Failed correction indexing/partial cutover preserves validated indexes (Tasks 2/3/5).

## Task 1: Artifact Verification and GPU Contracts

**Files:**

- Create: `infrastructure/inference/disclosure/runtime.py`, `requirements.txt`, `model-manifest.json`, `README.md`
- Create: `infrastructure/inference/disclosure/tests/test_contract.py`
- Create: `docker/disclosure-models.Dockerfile`
- Modify: both services' `settings.py`, `pyproject.toml`

**Interfaces:**

- FastAPI `app` in runtime.py implements spec §5 identity/document-embeddings/query-embedding/rerank contracts.
- Manifest: `model`, `revision`, `adapter_repo`, `adapter_revision`, `adapter_sha256`, `config_sha256`, `dimensions`, `transform_version`, `eval_tokens`; reranker uses quantization/prompt_version instead of dimensions.
- Preserve `DISCLOSURE_EMBEDDING_*`, adding revisions/hashes; use `DISCLOSURE_RERANKER_*` separately. No old Qwen query_instruction on Context.

- [ ] Inspect actual HF files/commits and verify last hash/nonzero LoRA. If IDs are unavailable, finish local contracts without claiming live integration.
- [ ] Test wrong hashes, zero LoRA B, dimension errors, duplicate/missing IDs, 16,385-token rejection, and company separation using fake encoders.
- [ ] Run `uv run pytest infrastructure/inference/disclosure/tests/test_contract.py -q`; confirm missing-implementation failures.
- [ ] Implement pinned base+PEFT loading, separate encoding paths, evaluated transformations, NF4 reranking. Load models and run sample forward passes during initialization.
- [ ] Pass contract tests; actual GPU validation remains Task 6.
- [ ] Review relevant files and commit: `feat: define disclosure model inference contracts`.

## Task 2: Structured Facts, Sections, and Narrative Storage

**Files:**

- Create: `infrastructure/postgres/migrations/versions/<next_revision>_disclosure_serving.py`; select revision after `alembic heads`.
- Create: `services/disclosure-indexer/src/disclosure_indexer/structured.py`
- Modify: `services/disclosure-indexer/src/disclosure_indexer/documents.py`, `chunks.py`, `index.py`
- Create: `services/disclosure-indexer/tests/test_structured.py`, `test_serving_schema.py`

**Interfaces:**

- `financial_rows(payload: dict[str, object]) -> list[dict[str, object]]` in structured.py preserves source fields/NUMERIC values.
- Create spec §6 tables: `disclosure_financial_facts`, `disclosure_sections`, `disclosure_narrative_chunks`. Change indexes' dimensions=2000 constraint to explicit 1024/2000 support and add identity JSONB. Validate table/index dimensions on insertion/activation.
- Keys include receipt_id plus source path/row order, preserving multiple scopes for one account.

- [ ] Test consolidated/separate names, prior/current/cumulative periods, units/currencies, negative/missing/zero values, nested tables, source offsets, correction deduplication. Preserve unsupported numeric strings and mark normalization failures.
- [ ] Confirm missing-table/column failures.
- [ ] Prefer DART structured responses; retain source-table JSON/positions. Do not invent account mappings. Parse with BeautifulSoup.
- [ ] Implement migration/extraction; verify upgrade/storage/reads/downgrade in dedicated PostgreSQL. New vectors must never enter old 2000-dimensional columns.
- [ ] Run old/new indexer tests; commit: `feat: store disclosure facts sections and narrative vectors`.

## Task 3: Daily Incremental Indexing

**Files:**

- Modify: `services/disclosure-indexer/src/disclosure_indexer/{collect.py,index.py,chunks.py,__main__.py,settings.py}`
- Create: `services/disclosure-indexer/src/disclosure_indexer/inference.py`
- Modify: `services/disclosure-indexer/tests/{test_collect.py,test_index.py,test_chunks.py,test_main.py}`

**Interfaces:**

- `embed_documents(client: httpx.Client, settings: Settings, documents: list[dict[str, object]]) -> dict[str, list[float]]` validates identity/counts/finite values and maps chunk_id to vectors.
- Retain `index_documents(engine, settings)`; select eligible work before inference.
- Signatures include source/parser/group/model identities. New failures retain ready old indexes.

- [ ] Test new/corrected reports, retries, parser changes, expired leases, missing archives. No eligible work: `GPU calls == 0`; embedding retries only: `GPU calls > 0`.
- [ ] Test changed paragraphs re-encode affected groups without mixing documents; verify current implementation fails.
- [ ] Implement paragraph/table-aware splitting and tokenizer budgets within 16,384 tokens. Persist parser/section-rule narrative classification; no GPU LLM classifier. Preserve unclassified text in source/sections but exclude it from vector search.
- [ ] Implement lease renewal, bounded retries, atomic activation; preserve other documents' successes when one fails.
- [ ] Run full indexer tests; commit: `feat: index changed narrative disclosures only`.

## Task 4: Direct Reads and Top-5 Evidence

**Files:**

- Modify: `services/disclosure-retrieval/src/disclosure_retrieval/{routes.py,repository.py,settings.py,app.py}`
- Create: `services/disclosure-retrieval/src/disclosure_retrieval/inference.py`
- Modify: `services/disclosure-retrieval/tests/{test_app.py,test_disclosures.py}`
- Modify: `services/portfolio-builder/src/portfolio_builder/{retrieval_client.py,disclosures.py,tools/disclosures.py}` and corresponding tests

**Interfaces:**

- `embed_query(client: httpx.Client, settings: Settings, query: str) -> list[float]`.
- `rerank_candidates(client: httpx.Client, settings: Settings, query: str, candidates: list[dict[str, object]]) -> list[dict[str, object]]`.
- Preserve reports/latest_evidence/historical_evidence separation; add spec §6 provenance/scores. Separate latest/history searches each use 50 candidates and requested top_k; meter calls separately.
- GET financials/sections use bound company/period/scope filters without GPUs.

- [ ] Test zero GPU calls for direct reads, company/period/scope filters, and exclusion of corrections after as_of.
- [ ] Test 0/1/fewer-than-50 candidates, chunk_id tie order, default five results, missing/duplicate candidates and unknown reranker IDs.
- [ ] Confirm failing tests for empty success=200, GPU timeout/OOM/identity mismatch=503, over-budget input=422.
- [ ] Inject app settings; implement filtered exact cosine/GPU reranking/source responses. Do not hold DB transactions during GPU waits or interpolate user input in SQL.
- [ ] Connect portfolio-builder without moving generation into retrieval. Preserve submission guards on search failure and valid empty-result handling.
- [ ] Run service/portfolio-builder tests; commit: `feat: retrieve disclosure evidence with trained reranking`.

## Task 5: Scheduling, Images, Configuration, and Cutover

**Files:**

- Modify: `scripts/run-morning-pipeline.sh`, `.env.example`, `README.md`, `docker/app.Dockerfile`
- Modify: `docker/disclosure-indexer.Dockerfile`, `docker/disclosure-retrieval.Dockerfile`, `docker/requirements/{disclosure-indexer.txt,disclosure-retrieval.txt,app.txt}`
- Modify in target dev: `compose.local.yaml`, `compose.prod.yaml`, `.github/workflows/ci-dev.yaml`, `tach.toml`
- Inspect `infrastructure/systemd/` before selecting the deployed news timer/script to modify.

- [ ] Document exact once-daily time/invocation after comparing timers and morning pipeline. Do not copy worktree compose.dev into dev.
- [ ] Verify no duplicate runs, other morning jobs continue after indexer failure, and failed exit status is preserved.
- [ ] Document HF identity/endpoint/timeouts. Measure provider startup before choosing limits: cold starts may exceed existing 30 seconds. Align clients/leases; no infinite waits.
- [ ] Verify lock/exports and image boundaries; GPU dependencies stay outside app image.
- [ ] Build/validate a separate narrative index before cutover. On failure disable the new path and preserve data; no new-query/old-vector fallback.
- [ ] Run Ruff lint/format, relevant pytest, diff checks, image builds, Compose config; distinguish unrun checks/existing failures.
- [ ] Commit reviewed changes only: `chore: wire disclosure serving schedule and deployment`.

## Task 6: GPU and Service Validation

**Files:**

- Create: `infrastructure/inference/disclosure/benchmark.py`
- Create: `docs/operations/2026-10-28-disclosure-serving-validation.md`
- Raw data, weights, source disclosures remain outside the repo; document paths/hashes/summaries.

- [ ] Verify HF identity, provider accounts/endpoints, cost ceiling and GPU availability. Confirm concrete limits/settings before paid execution.
- [ ] Measure combined models on 32GB at maximum input and 50 candidates, including sequential requests. On OOM compare 40/48GB or split workers; no silent CPU fallback.
- [ ] Compare identical evaluation/service vectors/scores; record dtype differences/tolerances and halfvec Top-5 effects.
- [ ] Test Modal snapshots and Runpod FlashBoot on matching inputs/models: first start, restore, fresh start, warm. Record sample counts, p50/p95, allocation wait, initialization, query embedding, DB, reranking, end-to-end time. Short trials do not establish daily behavior.
- [ ] Evaluate calibration200 through the service, separating 34 answerable narrative cases and unanswerable cases. Prior 1.0000/0.9783/0.9706 are references, not guarantees. Freeze configuration/data/weights before separate final178 evaluation.
- [ ] Meter GPU execution/idle/loading/storage/CPU/DB costs for 100 randomly timed daily requests; separate assumptions from measurements in the Drive cost table.
- [ ] Document provider decision, unresolved issues, reindexing/rollback; commit: `docs: record disclosure serving validation`.

## Review and Open Items

Spec coverage: model identity → Task 1; storage → 2; incremental work → 3; direct reads/evidence-only retrieval → 4; scheduling/cutover → 5; measured quality/latency/cost → 6.

Actual HF IDs/commits, provider, timer schedule and deployed DB head remain unverified. APIs/new tables are proposals, not existing implementations. No code tests, deployments, or paid inference were run while writing this plan.
