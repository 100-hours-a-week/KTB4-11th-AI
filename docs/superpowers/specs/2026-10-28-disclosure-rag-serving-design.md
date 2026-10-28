# Disclosure Indexing, Retrieval, and Fine-Tuned Model Serving Design

Date: 2026-10-28

Status: Existing implementation and agreed requirements documented. Target changes are not implemented or deployed. The hosting provider and Hugging Face repository IDs remain undecided.

## 1. Purpose and Earlier Documents

Keep `services/disclosure-indexer` for collection/storage and `services/disclosure-retrieval` for evidence retrieval. Use Colab for training and evaluation only. Services call a separate GPU runtime that loads weights from Hugging Face.

This document extends the history in `2026-10-16-disclosure-rag-cleanup-design.md`. It replaces the 2000-dimensional Qwen and vector-only decisions in the root checkout's `2026-10-18-disclosure-rag-integration-design.md`. Earlier test results do not validate these changes.

Documentation lives in `.codex/worktrees/disclosure-rag`. This worktree and root dev have different uncommitted changes. Compare changes before integration; do not overwrite files or revert existing work. Deleting the news-preprocessor disclosure publisher is outside this request.

## 2. Existing Implementation and Target

| Area | Observed implementation | Target |
| --- | --- | --- |
| Collection | Periodic reports, source archives, JSONB | Once-daily check; new reports, corrections, retries |
| Embedding | Fixed 2000 dimensions, independent HTTP strings | Context score10, 1024 dimensions, document context groups |
| Chunking | Byte splitting/overlap within sections | Paragraph/table boundaries, source positions, tokenizer budgets |
| Storage | Reports, documents, indexes, chunks, progress | Direct facts/sections plus separate narrative vectors |
| Retrieval | Worktree vector search, top_k=6, no reranker | Filtered exact cosine top-50 → Qwen reranker → default Top-5 |
| Identity | Model name, dimensions, chunk settings | Base/adapter revisions and hashes, transformation/chunking versions |
| Deployment | External embedding HTTP server | Separate HF-backed GPU runtime; on-demand inference |

Root dev has exact-search changes absent from this worktree. Compare both repository.py files and remove remaining ANN settings. Do not truncate old 2000-dimensional vectors and reuse them as new-model vectors.

## 3. Service Responsibilities

### disclosure-indexer

- Check OpenDART once daily at the news collection job's scheduled time. `news-http` is a long-running HTTP server, not a cron job.
- Reconcile the existing morning-pipeline invocation to avoid duplicate runs. Inspect the deployed timer and document the exact KST schedule.
- Distinguish new receipt IDs, corrections, and source-hash changes; preserve correction relationships and publication times.
- Query eligible work before calling the GPU. With neither new work nor eligible retries, exit with zero model calls.
- Retry eligible failures even without new reports, using bounded attempts and leases.
- Archive ZIP/XML outside the repository; store hashes, parsed content, structured rows, sections, and provenance in PostgreSQL.
- Embed narrative passages only. Structured content and full sections remain readable without vectors.

### disclosure-retrieval

- Search only on request; starting the CPU API does not require an always-running GPU.
- Read financial values from structured rows, explicitly identified content from sections, and narrative content of unknown location through RAG.
- Rerank up to 50 candidates and return five by default; process fewer when fewer exist.
- Generate no answers, summaries, or investment decisions. Another service's LLM reads the evidence.
- Distinguish successful empty results from failures. No results do not establish absence of risk.

## 4. Selected Artifacts

| Field | Embedding | Reranker |
| --- | --- | --- |
| Base | `perplexity-ai/pplx-embed-v2-context-9b-preview` | `Qwen/Qwen3-Reranker-4B` |
| Revision | `e2c06a779d45e8db53066735be1b98ffe3412ca3` | `22e683669bc0f0bd69640a1354a6d0aebcfeede5` |
| Adapter | `context-v5-1024-score06-pilot-01/adapter-step-000010` | `qwen4-rank-v2/adapter-last` |
| Weight SHA256 | `1f5d43671826dab233cc32942cedb31bd0f8564f42a4b8f359a4a100d66c60e2` | `7d43c402f5061930e5e9fbc6e8c9e7618662814181f50a8c1b7a48371f947d24` |
| Settings | 1024 dimensions; 16,384-token evaluation limit | NF4; 16,384-token evaluation limit |

1024 is the vector dimension, not chunk length. Upload both `adapter_model.safetensors` and `adapter_config.json` per model. Pin HF repository IDs and commit revisions. Supply private-repository tokens through environment variables only.

Drive's current `adapter-best`, hash `015b82…`, has 144 all-zero LoRA B tensors and differs from the evaluated step68 hash `4cd52c…`. Reinspection on 2026-10-28 confirmed this. Do not choose it by filename. The user uploaded best to HF; inspect actual HF files and verify replacement with both last-adapter files before deployment.

Do not mix vectors across adapters sharing a base name. At startup validate hashes, nonzero/finite LoRA values, dimensions, and transformations. Never append outputs to an incompatible index.

## 5. Encoding and GPU API

Proposed implementation contracts; uploading HF weights does not create these endpoints:

- `GET /v1/identity`: Base/adapter revisions and hashes, dimensions, transformation version, token limit.
- `POST /v1/disclosure/document-embeddings`: Accept `{identity, documents: [{document_id, chunks: [{chunk_id, text}]}]}`; return 1024-dimensional vectors preserving IDs/order.
- `POST /v1/disclosure/query-embedding`: Accept `{identity, query}`; return a 1024-dimensional vector.
- `POST /v1/disclosure/rerank`: Accept `{identity, query, candidates: [{chunk_id, text, metadata}]}`; return per-candidate yes/no logit difference and sigmoid score.

Provider authentication/queue handling belongs in transport. Validate JSON independently of the provider. Put GPU code in `infrastructure/inference/disclosure/`. Do not execute Colab/Drive files or install GPU libraries into regular service/application images.

Use Context's distinct document/query paths and fixed prefixes, without duplicating the old Qwen instruction. Do not replace grouped encoding with independent chunk calls. Document order and context groups affect vectors; paragraph changes require re-encoding affected groups.

Preserve paragraphs where possible; split long paragraphs/tables with source positions intact. Version tokenization/grouping, record truncation, and split or explicitly reject inputs beyond 16,384 actual tokenizer tokens.

Preserve evaluation transformations: `tanh → multiply by 127 → round/clip → first 1024 dimensions → L2 normalization`. INT8 output transformation is not INT8 weight quantization. Preserve reranker metadata, preceding context, instructions, and template; score using yes/no logits.

## 6. Storage and Responses

Do not edit migration 0011; it may already be applied. Inspect Alembic head and add a migration.

- Reuse receipt IDs, sources, states, and leases in disclosures/documents/indexes.
- Add `disclosure_narrative_chunks` with `halfvec(1024)`, source text/positions, and context groups. Retain existing 2000-dimensional chunks until validated cutover.
- Add `disclosure_financial_facts`: receipt, company, period, consolidated/separate scope, statement type, account ID/name, currency/unit, exact values, source JSONB. Preserve source strings and normalized NUMERIC; missing is not zero.
- Add `disclosure_sections`: document order, heading path, text, and source positions. Direct reads must work independently of embedding success.
- Index identity includes source hash, parser, chunker, grouping, model, adapter, dimensions, and transformation versions. Activate completed indexes transactionally.

Keep `POST /stocks/{stock_code}/disclosures/search` for narrative RAG; set default top_k=5. Filter company/publication time/period first, then exact cosine top-50 over all eligible narrative chunks. No initial HNSW/RRF/MMR. Do not reuse distance 0.5 without validation; no default score threshold on the new path.

Add `GET /stocks/{stock_code}/disclosures/financials` and `GET /stocks/{stock_code}/disclosures/sections`. Require explicit period/scope/account or section filters and bound SQL parameters. Do not expose arbitrary SQL or merge unspecified scopes.

Evidence includes `receipt_id`, `chunk_id`, `stock_code`, `period_end`, `section`, `document_name`, `start_offset`, `end_offset`, `text`, `source_url`, `cosine_score`, `reranker_logit`, `reranker_score`, `index_identity`. Offsets are Unicode character positions in parsed source text, using [start, end). Preserve source archives/parser versions. Direct responses also include receipt, period, scope, and provenance.

Scores are not answer probabilities. No evidence returns 200/empty list; GPU timeout/OOM/identity mismatch returns 503; invalid/over-budget input returns 422. No silent CPU fallback or generated answers masking failure. Bound retries and waits.

## 7. Hosting and Costs

Assume 100 randomly timed searches/day over 24 hours. Exclude structured/section reads from GPU calls; account separately for CPU retrieval and DB costs.

Options: always-running Pods reduce startup waits but cost idle time; on-demand Pods require allocation/loading; Serverless is the proposed first experiment. Neither Runpod nor Modal is selected.

Measure combined models on 32GB first. If insufficient, compare 40/48GB and split workers. Measure peak VRAM/host RAM during load, maximum input, and 50-candidate reranking, plus output consistency. 32GB feasibility is unmeasured.

Test Modal GPU snapshots and Runpod FlashBoot with the actual container. Separate first startup, restore, and fresh start after a restore miss. Advertised seconds are not our measurements. Earlier 5/30/60/120/180-second cost inputs are assumptions.

- [GPU costs and inputs](https://docs.google.com/spreadsheets/d/1ne6Z1lXMfF1rKvVB93w36Hj0kWQ-EX9WhFCPxOFwjhk/edit)
- [Hosting cost and startup explanation](https://docs.google.com/document/d/1cKUcbLhc1Sn4ez6XCdgt6y82BnTneBzy_a9JDEf8h9c/edit)
- [Modal snapshot limitations](https://modal.com/docs/guide/memory-snapshots)
- [Runpod endpoint configuration](https://docs.runpod.io/serverless/endpoints/endpoint-configurations)

## 8. Evaluation Evidence and Acceptance

The 2026-10-23 record reports Recall@5=1.0000, nDCG@5=0.9783, MRR=0.9706 for Context score10 + Qwen last on 34 answerable narrative questions within calibration200 from new semiannual reports. These are not all-200 or final178 results. Search timing excluded query embedding and production pgvector network time. Structured evaluation supplied SQL arguments; it does not guarantee operational accuracy of 1.0.

Re-evaluate changed production paragraph chunking, narrative classification, halfvec storage, and context grouping. Generation/RAGAS judges remain offline.

Acceptance criteria:

1. Actual HF hashes match selected artifacts; both LoRAs are applied.
2. Zero new/eligible retry work means zero GPU calls; corrections/retries are retained.
3. Direct reads preserve source, period, and unit without GPUs.
4. Identical document/query vectors and scores match references within measured tolerances recorded in validation artifacts.
5. Measure Top-5 quality, unanswerable behavior, first/restored/warm end-to-end p50/p95, failure rate, daily cost, and memory.
6. Validate migration/cutover/rollback, real DB reads, builds, Compose, and relevant tests. Never test against production DB.

## 9. Documentation Scope

Only spec and plan are written. No service code, weight uploads/replacements, deployments, paid GPU execution, or training are performed. Before implementation verify HF IDs/revisions, provider accounts/endpoints, deployed migration state, and timers.
