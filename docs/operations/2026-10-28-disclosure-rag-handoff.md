# Disclosure RAG: Handoff

Date: 2026-10-28

Continuation: [October 29 Modal integration](2026-10-29-disclosure-modal-integration.md).
The findings and deployment numbers below describe the earlier branch state; consult the
continuation for implemented fixes and remaining live-GPU checks.
Branch: `feat/346-disclosure-rag`, 12 commits on top of `origin/dev` (`b5d43b2`), 93 files, +8438/-9
Worktree: `.claude/worktrees/346-disclosure-rag`
Issue: [#346](https://github.com/100-hours-a-week/KTB4-11th-AI/issues/346)

Not pushed. Not opened as a pull request.

## 1. What To Know First

**The branch was re-based onto current `origin/dev` partway through.** The issue #346 work existed only as uncommitted files in the root checkout, cut from a tree 220 commits behind. It was re-applied on top of `origin/dev` rather than merged forward. If you have a copy from before, discard it — three divergent copies existed (`.claude/worktrees/disclosure-rag`, `.codex/worktrees/disclosure-rag`, and the root checkout) and only this branch is current.

**Two defects stopped either service from running at all,** and both were invisible locally because every database test skipped. They are fixed; see §3.

**Local test runs need `scripts/run-tests.sh`, not `uv run pytest`.** `uv run` re-applies the macOS `UF_HIDDEN` flag to the workspace `.pth` files on every invocation, and Python 3.13 silently skips hidden `.pth` files, so no workspace package imports and the suite collects 0 tests with 93 errors. The script puts the source roots on `PYTHONPATH` instead. This is environment-specific; Linux CI is unaffected.

## 2. Current State

```
./scripts/run-tests.sh                      # 1252 passed, 48 skipped (no DB)
KTB_TEST_POSTGRES_DSN=... ./scripts/run-tests.sh   # same, with all disclosure DB tests running
ruff check . && ruff format --check .       # clean
tach check && tach check-external -e services      # clean
deptry (both disclosure services)           # clean
alembic upgrade head                         # single head 0015, round-trips to 0011
docker build (both disclosure Dockerfiles)   # both build
```

The 48 skips are QuestDB, Valkey, tracing and SSM tests that need other services. **No disclosure test skips.**

Test database used throughout:

```bash
docker run -d --name ktb-test-pg -e POSTGRES_USER=ktb -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=ktb_test -p 55432:5432 pgvector/pgvector:0.8.6-pg18-trixie
export KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:test@127.0.0.1:55432/ktb_test
export KTB_POSTGRES_DSN=$KTB_TEST_POSTGRES_DSN   # alembic uses this name
```

## 3. Defects Fixed

Each was reproduced before being fixed. Ordered by severity.

| | Defect | Evidence | Commit |
| --- | --- | --- | --- |
| D1 | Migration declared `revision = "0011"`, which `dev` already used for `0011_add_corporation_sector`. `alembic upgrade head` failed outright: *Multiple head revisions are present*. Renumbered to `0012`; the file had never been committed on any branch, so no database could have applied it. | `alembic heads` showed `0011 (head)` twice | `70ab01a` |
| D2 | `index_documents` probed the vector column with `atttypmod - 4`. pgvector stores the dimension directly, so `halfvec(2000)` read back as 1996 and every indexing run aborted before processing anything. Replaced with `format_type`/`to_regclass`, the pattern already in `infrastructure/postgres/tests/test_migrations.py`, which also catches a `vector`-vs-`halfvec` mismatch. | `SELECT atttypmod` on a real `halfvec(2000)` returned 2000; the previously-skipped test then failed with `configured=2000, database=1996` | `70ab01a` |
| D3 | Neither service appeared in `compose.prod.yaml`, and the shared postgres anchor named the other eleven services but not these two, so both would fail settings validation at startup. Added both DSNs to the anchor and both service blocks. | anchor lines 15–27 | `28974e4` |
| D4 | `/health` returned `ok` while PostgreSQL was unreachable, so Compose called the service healthy and portfolio-builder started behind a dependency that could not answer. Now runs `SELECT 1` and answers 503. | | `28974e4` |
| D5 | Search results dropped `receipt_id` and `chunk_id` (already selected) and never selected `document_name`, `start_offset`, `end_offset`. A result could not be traced to its filing, which issue #346 requires. Offsets restart per file inside an archive, so `document_name` is what makes them interpretable. | | `25b5284` |
| D6 | One report with an active index from a different model raised, turning a partially re-indexed stock into a 503 for every search and making a model swap an outage. Each report now reports `index_state` as `ready`/`pending`/`other_space`, and the search returns the evidence that does match. `index_status` renamed to `index_state`. | | `25b5284` |
| D7 | `max_distance` defaulted to 0.5, which the design forbids carrying forward unvalidated, and a silent cutoff made "nothing relevant" and "filtered out" indistinguishable. Default removed; every response reports `latest_searched`/`latest_returned`. | | `25b5284` |
| D8 | `disclosure_indexes` had no attempt counter, so a document that always failed to embed was retried forever — contradicting the acceptance criterion that no eligible work means no GPU calls. Migration `0013` adds bounded `attempts` and `dead_letter`. | test drives a document to `dead_letter` then asserts zero further embed calls | `0a44a0a` |
| D9 | Embedding input reserved the chunk body first and byte-sliced the metadata into the remainder with `errors="ignore"`, so a tight budget cut the company and period to a few characters and nothing recorded it. Company and period are now required to fit; heading and table context take what is left, trimmed on a character boundary and logged. | reproduced: budget 9010 produced `'삼성전\n가가가…'` | `0a44a0a` |
| D10 | The index signature ignored `DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION`, so changing the query prefix changed the query vector space while every stored index kept matching. Now part of the signature. | | `0a44a0a` |
| D11 | A disclosure re-published under the same receipt number was invisible: the receipt was already `ready`, so nothing fetched it again to notice the new hash. `refresh_sources` re-fetches receipts published within `refresh_days` (30) and re-stores only those whose bytes changed. | | `23ab660` |
| D12 | A completed collection window was never re-listed after seven days, so a correction filed later in the season was never collected. Now `recheck_days`, default 400. | | `23ab660` |
| D13 | `report_period` raising on a title was swallowed by a bare `continue`. The closing month is re-read from DART every run, so a company that changed its fiscal year silently stopped yielding its own history. Now counted as `unclassified` and logged. | reproduced: `closing=3` on `"반기보고서 (2026.06)"` raises | `23ab660` |
| D14 | The `try` wrapped the whole window loop, so one transient listing failure cost every later window in that run. Boundary moved to one window. | | `23ab660` |
| D15 | Reconciliation selected every disclosure on every run with no limit, and a document that legitimately parses to `unavailable` was re-parsed forever. Now bounded by `document_limit`, and `unavailable` counts as settled for the current parser. | | `23ab660` |
| D16 | `search_reports` took the HTTP request model as `Any`, coupling the repository to the controller's schema. Now takes a `SearchParams` dataclass. | | `25b5284` |

Two pre-existing test failures were also exposed and fixed (`0a44a0a`, `25b5284`): `test_agent.py`'s two trace tests use the DB fixture, so they had always skipped, and nobody had seen that `RequireDisclosures` adds a tool result to turn 1.

## 4. Features Added

**GPU-free structured reads** — the design's claim that structured reads need no GPU did not hold, because reported values were only reachable through narrative vector search.

- Migration `0014`, `disclosure_sections`: sections promoted from `disclosure_documents.documents` JSONB to rows, written in the same transaction that sets extraction state and replaced wholesale on re-parse, so they survive an embedding failure and never duplicate. `GET /stocks/{code}/disclosures/sections` requires an explicit `receipt_id`.
- Migration `0015`, `disclosure_financial_facts`: OpenDART `fnlttSinglAcntAll.json`, consolidated (CFS) and separate (OFS) stored as distinct facts and never merged. Source string kept verbatim beside a normalized `NUMERIC`; an empty field or dash is nil so `value` is null and never zero; anything else that will not parse keeps its string and reports `normalized=false`. Only the current term is stored — prior-term columns belong to the reports that filed them. `GET /stocks/{code}/disclosures/financials` requires an explicit `period_end` or `business_year`.

Both have tests asserting they answer with no embedding configuration present and that `urlopen` is never called.

**Modal inference runtime** — `infrastructure/inference/disclosure/`, deliberately not a workspace package. torch, transformers, peft and bitsandbytes are declared only in the Modal image; the Modal client stays a local tool. See §6.

## 5. Conventions Followed, And One Reversal

House conventions applied: `controllers/` + `repositories/` + `repositories/tables.py` as in `news-http`; frozen dataclasses for rows; `sa.Table` + `sa.select()` rather than hand-built SQL; `start_logging` + `ExitStack` in the cron entry point, which `disclosure-indexer` was the only one of eight services to lack.

**One query stays raw SQL on purpose.** The narrative vector search needs PostgreSQL's `AS MATERIALIZED` hint to force the exact search issue #346 requires, and SQLAlchemy 2.1 cannot express it. `test_exact_search_scores_every_filtered_chunk_before_top_k` asserts both the hint and the resulting EXPLAIN plan (601 rows scored). `news-http/repositories/graph.py` uses raw SQL for the same kind of reason. What was fixed is the readability: the old code concatenated SQL fragments that split mid-identifier.

**A reversal worth knowing:** `repositories/tables.py` was first added to `disclosure-retrieval` purely to match convention, and deptry caught that it pulled in `pgvector` as a new runtime dependency for a module nothing imported. It was deleted, then re-added later when `sections.py` and `financials.py` actually needed it. Table definitions now exist only where they are used.

## 6. Modal Serving

Deployed: app `disclosure-inference`, workspace `djdj1473`, `us-east`, one container per model on an **L4 (24GB)**, scale to zero after 120s.

Full measurements: [`2026-10-28-disclosure-modal-measurements.md`](2026-10-28-disclosure-modal-measurements.md). Design: [`2026-10-28-disclosure-modal-serving-design.md`](../superpowers/specs/2026-10-28-disclosure-modal-serving-design.md).

### Artifacts verified, not assumed

Both adapters on Hugging Face match the design's selected hashes exactly, and every LoRA B tensor is nonzero and finite:

| | Repo @ revision | SHA256 | LoRA |
| --- | --- | --- | --- |
| Embedding | `Dongju-00/stockspoon-pplx-embed-v2-context-9b` @ `6949b167` | `1f5d4367…` | 152 A / 152 B |
| Reranker | `Dongju-00/stockspoon-qwen3-reranker-4b-lora` @ `8cfbbb0c` | `7d43c402…` | 144 A / 144 B |

The reranker has exactly the **144** LoRA B tensors the design recorded as all-zero in the rejected `adapter-best`; all 144 are nonzero here, so the repo holds the evaluated `adapter-last` weights. **This closes the design's open replacement warning.** Both repos are public, so the runtime needs no HF token.

### Three corrections from reading the checkpoint's own code

`modeling_pplx_contextual.py` at the pinned revision changed the implementation materially. Each would have produced wrong vectors:

1. **The model is not a generic encoder.** `encode(documents, task=...)` takes `list[list[str]]`, joins a document's chunks with `"[D] "` and `"<|chunk_sep|>"`, encodes the document as one sequence and pools each chunk back out by character span. The grouped context encoding the design asks for is the checkpoint's own contract. Hand-rolled mean pooling over `last_hidden_state` was wrong and is gone.
2. **Clip range is `[-128, 127]`,** not `[-127, 127]` as the earlier design recorded. After `tanh × 127` the lower bound never binds, but it is kept identical because a difference here is a difference in every stored vector.
3. **`embedding_dim` is 2048,** so "the first 1024 dimensions" is a truncation, and it must happen **before** normalization. `encode` is called with `normalize_embeddings=False`; the service truncates then normalizes. Normalizing 2048 first would scale every stored vector by a factor the evaluation never applied.

Also: the checkpoint was saved with **transformers 5.3.0** and its remote code imports `Qwen3_5Model`, absent from 4.x. The first deploy failed on a `huggingface_hub` pin taken from the local machine. Working set: torch 2.14.1, transformers 5.3.0, peft 0.21.2, accelerate 1.15.0, bitsandbytes 0.50.2, huggingface_hub 1.33.0.

### The numbers that matter

| | Embedder | Reranker |
| --- | --- | --- |
| VRAM reserved, max input | **21.28 / 22.06 GiB** (0.54 free) | 7.24 / 22.06 GiB |
| Warm | 0.34s (37 tok) / 13.0s (15,740 tok) | 6.74s (50 candidates) |
| Fresh start, no snapshot | 137.3s | 1570.9s |
| Snapshot restore p50 | **1.77s** (n=5) | **1.35s** (n=3) |
| Snapshot restore max | **222.7s** — a miss | **1371.3s** — a miss |

**24GB works but has no margin.** At 15,740 tokens the embedder reserves 96% of the card. One request at a time per container, no room to raise the token limit, and the two models could never share one card. Moving to 40/48GB buys margin and batching; that is a cost decision, not a correctness one.

**Snapshots decide viability.** At ~100 requests/day against a 120s scaledown, nearly every request is cold. Restore at the median is ~75× faster than a fresh start, so a cold search starts in under two seconds instead of over two minutes.

**But the distribution is bimodal, and both models missed.** One of five embedder samples took 222.7s; one of three reranker samples took 1371.3s. Two misses in eight samples. The reranker's miss is six times worse because on a miss it re-pays its base download, so a miss there is a 23-minute start, not a slow one. With n=5 and n=3 the max *is* approximately p95, so **p95 is a miss for both**. Eight samples do not establish a hit rate.

### Security note

The first deploy published all five endpoints **unauthenticated** — anyone with the URL could spend GPU on this account. All five now set `requires_proxy_auth=True`. A proxy auth token must be minted in the Modal dashboard before the services can call them; there is no CLI for it.

## 7. What Is Left

Ordered by what blocks what.

1. **Weights into the image build.** The reranker's warm-volume fresh start (1570.9s) was *worse* than its first (921.3s), and its restore miss cost 1371.3s — the volume does not retain its weights, so every miss re-pays the download. A `modal.Volume` commits in the background and on clean exit, so a killed container loses what it wrote. Downloading during the image build removes this and takes Hugging Face out of the startup path. On this evidence it is required, not an optimisation.
2. **A longer restore-rate run.** The miss rate is the number that decides whether `min_containers=1` earns its idle cost. Five samples cannot answer it.
3. **Wire the services to Modal.** Nothing calls the deployed endpoints yet. `disclosure-indexer` and `disclosure-retrieval` still use `embed.py` against `DISCLOSURE_EMBEDDING_BASE_URI`, and there is no reranker call anywhere — `top_k` defaults to 6 with no top-50 pool.
4. **The 1024-dimension cutover.** `settings.dimensions` is pinned `ge=2000, le=2000` and the column is `halfvec(2000)`. Serving two spaces at once additionally needs `disclosure_indexes_active_idx` relaxed, which permits one active index per document.
5. **`G-2`: ZIP file-boundary flattening in `index.py`.** `documents` JSONB holds every file in the archive and `index.py` flattens their sections into one list, so a batch can span two physical documents. Harmless today because encoding is independent per chunk — but it must be fixed **before** grouped encoding is wired, or documents will silently mix. Note the Modal runtime already enforces the boundary (`document_groups` rejects it), so the indexer is the side that needs changing.
6. **Parity and cost.** Identical input through the evaluation path versus the service, with a recorded tolerance and the halfvec effect on Top-5; and cost for 100 requests/day. Both are design §7 requirements still unmeasured.

## 8. Open Decisions

- **L4 or move up.** 0.54 GiB of headroom works but forbids concurrency and any token-limit increase.
- **`min_containers=1`** versus accepting a ~4-minute p95 on restore misses.
- **The renumbering assumption.** `0012` assumes no deployed database applied a migration named `0011_create_disclosures`. `git log --all` shows it was never committed anywhere, so this is near-certain, but worth confirming against the deployed `alembic_version` before release.

## 9. Files Worth Reading First

```
docs/superpowers/specs/2026-10-28-disclosure-rag-fix-design.md       verified defects
docs/superpowers/specs/2026-10-28-disclosure-modal-serving-design.md Modal + corrected transform
docs/operations/2026-10-28-disclosure-modal-measurements.md          all measurements
infrastructure/inference/disclosure/                                 the Modal runtime
services/disclosure-retrieval/src/disclosure_retrieval/repositories/disclosures.py
services/disclosure-indexer/src/disclosure_indexer/collect.py
```
