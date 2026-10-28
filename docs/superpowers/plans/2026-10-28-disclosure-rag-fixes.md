# Disclosure Defect Fix and GPU-Free Reads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the two disclosure services run against a real database, give search results a locatable source, and read financial values and sections without a GPU.

**Architecture:** `disclosure-indexer` stays a cron job that collects, archives, parses, and embeds. Parsing now also writes section rows and OpenDART financial facts, both independent of embedding success. `disclosure-retrieval` stays a read-only CPU API: `repository.py` keeps narrative vector search, a new `reads.py` serves direct structured and section reads. No service calls another service.

**Tech Stack:** Python 3.13 uv workspace, FastAPI, httpx, SQLAlchemy, PostgreSQL 18 + pgvector 0.8.6 (`halfvec`), BeautifulSoup, Alembic.

**Spec:** [Disclosure defect fix design](../specs/2026-10-28-disclosure-rag-fix-design.md), which extends [the serving design](../specs/2026-10-28-disclosure-rag-serving-design.md).

## Global Constraints

- Branch `feat/346-disclosure-rag`, cut from `dev`. Pull requests target `dev`.
- Do not change any service other than `disclosure-indexer`, `disclosure-retrieval`, and the `portfolio-builder` files that read disclosure evidence. Do not add to `packages/core`.
- The vector contract stays `halfvec(2000)`, `dimensions = 2000`, L2-normalized. No model, adapter, reranker, or GPU code.
- Keep comments and docstrings sparse, matching the surrounding services.
- Tests that need PostgreSQL use `KTB_TEST_POSTGRES_DSN` against a disposable database. Never a deployed one.
- After every `uv sync`, run `chflags nohidden .venv/lib/python3.13/site-packages/*.pth` or no workspace package imports on macOS.
- Dependency changes require `uv lock` plus the repository's `uv export` commands for `docker/requirements/`.
- Run `pytest`, `ruff check`, `ruff format --check`, `tach check` before each commit.

## Review Focus

Five conditions the spec implies that no task's happy path exercises. Each has its test named in the owning task.

1. A `halfvec` column of the wrong width must be rejected, not silently accepted — the `- 4` bug passed every mock-based test (Task 2).
2. A receipt whose archive holds two files must produce chunks and sections whose offsets are distinguishable per file, since offsets restart at zero (Tasks 5, 10).
3. A stock whose reports are split across two embedding spaces must return the matching half, not 503 (Task 8).
4. A financial value that does not parse as a number must keep its source string rather than becoming zero or null-with-loss (Task 11).
5. A company whose closing month changed must not silently stop yielding reports (Task 9).

---

### Task 1: Make the schema applicable

Unblocks every database test in the repository; nothing else can be verified until `alembic upgrade head` succeeds.

**Files:**
- Rename: `infrastructure/postgres/migrations/versions/0011_create_disclosures.py` → `0012_create_disclosures.py`
- Modify: that file's `revision` and `down_revision`
- Test: `infrastructure/postgres/tests/test_migrations.py`

**Interfaces:**
- Produces: a single Alembic head, `0012`. Later migrations chain from it.

- [ ] **Step 1: Write the failing test** in `infrastructure/postgres/tests/test_migrations.py`

```python
def test_single_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert len(heads) == 1, heads
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `uv run pytest infrastructure/postgres/tests/test_migrations.py::test_single_alembic_head -v`
Expected: FAIL, two heads both named `0011`.

- [ ] **Step 3: Renumber the disclosure migration**

`git mv` the file to `0012_create_disclosures.py`, set `revision = "0012"` and `down_revision = "0011"`, and update the `Revision ID` / `Revises` lines in its docstring. Leave every table definition untouched.

- [ ] **Step 4: Confirm the test passes and the schema applies**

Run: `uv run pytest infrastructure/postgres/tests/test_migrations.py -v`
Then, with `KTB_POSTGRES_DSN` pointing at an empty disposable database:
`uv run alembic upgrade head` then `uv run alembic downgrade 0010` then `uv run alembic upgrade head`
Expected: all three succeed with no warning about a duplicate revision.

- [ ] **Step 5: Run the whole suite against the database**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest`
Expected: the 241 skips drop and the disclosure database tests execute. Record any that fail — they are real findings, and the next tasks own them.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/postgres
git commit -m "fix: chain the disclosure migration after the applied 0011 head"
```

---

### Task 2: Read the vector column width correctly

**Files:**
- Modify: `services/disclosure-indexer/src/disclosure_indexer/index.py:37-49`
- Test: `services/disclosure-indexer/tests/test_index.py`

**Interfaces:**
- Produces: `column_dimensions(conn: sa.Connection) -> int | None` in `index.py`, returning the declared width of `disclosure_chunks.embedding`.

- [ ] **Step 1: Write the failing tests** in `services/disclosure-indexer/tests/test_index.py`

Both need a real database, so mark them with the existing `pg_engine` fixture.

```python
def test_column_dimensions_reads_the_declared_halfvec_width(pg_conn):
    pg_conn.execute(sa.text("CREATE TEMP TABLE disclosure_chunks (embedding halfvec(2000))"))
    assert column_dimensions(pg_conn) == 2000


def test_indexing_rejects_a_column_of_another_width(pg_engine, settings):
    # disclosure_chunks.embedding altered to halfvec(1024)
    with pytest.raises(RuntimeError, match="dimension mismatch"):
        index_documents(pg_engine, settings)
```

- [ ] **Step 2: Run them and confirm the first fails**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer/tests/test_index.py -k dimensions -v`
Expected: FAIL — returns 1996 for a `halfvec(2000)` column.

- [ ] **Step 3: Implement `column_dimensions(conn: sa.Connection) -> int | None`**

Parse the width out of `format_type(atttypid, atttypmod)`, the pattern already used in `infrastructure/postgres/tests/test_migrations.py`, so the query does not depend on how one extension encodes its typmod. Return `None` when the column is absent. Call it from `index_documents` in place of the inline query.

- [ ] **Step 4: Confirm both tests pass**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer/tests/test_index.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/disclosure-indexer
git commit -m "fix: read the halfvec column width from format_type"
```

---

### Task 3: Start both services in production

**Files:**
- Modify: `compose.prod.yaml:133-190`
- Modify: `services/disclosure-retrieval/src/disclosure_retrieval/app.py`
- Test: `services/disclosure-retrieval/tests/test_app.py`

**Interfaces:**
- Produces: `/health` returns `{"status": "ok"}` on `SELECT 1` success and HTTP 503 otherwise.

- [ ] **Step 1: Write the failing tests** in `services/disclosure-retrieval/tests/test_app.py`

```python
def test_health_reports_ok_when_the_database_answers(client):
    assert client.get("/health").status_code == 200


def test_health_reports_unavailable_when_the_database_is_unreachable(broken_client):
    assert broken_client.get("/health").status_code == 503
```

Add a `broken_client` fixture whose engine points at a closed port.

- [ ] **Step 2: Run them and confirm the second fails**

Run: `uv run pytest services/disclosure-retrieval/tests/test_app.py -k health -v`
Expected: FAIL — returns 200 with no database.

- [ ] **Step 3: Make `/health` check the database**

In `create_app`, run `SELECT 1` on `app.state.engine` and raise `HTTPException(503)` on `sqlalchemy.exc.SQLAlchemyError`.

- [ ] **Step 4: Add both DSNs to `compose.prod.yaml`**

Give `disclosure-indexer` `DISCLOSURE_INDEXER_POSTGRES_DSN` and `disclosure-retrieval` `DISCLOSURE_RETRIEVAL_POSTGRES_DSN`, built from `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` exactly as the other eleven services in the `x-postgres-environment` anchor do. Add them to the anchor so a thirteenth service cannot repeat this omission.

- [ ] **Step 5: Verify the Compose files resolve**

Run: `docker compose -f compose.prod.yaml config >/dev/null` and `docker compose -f compose.local.yaml config >/dev/null`
Then: `uv run pytest services/disclosure-retrieval -v`
Expected: both configs valid, tests pass.

- [ ] **Step 6: Commit**

```bash
git add compose.prod.yaml services/disclosure-retrieval
git commit -m "fix: inject disclosure database DSNs and check the database in health"
```

---

### Task 4: Return the evidence location

**Files:**
- Modify: `services/disclosure-retrieval/src/disclosure_retrieval/repository.py:110-159`
- Modify: `services/portfolio-builder/src/portfolio_builder/disclosures.py`
- Test: `services/disclosure-retrieval/tests/test_disclosures.py`, `services/portfolio-builder/tests/test_retrieval_client.py`

**Interfaces:**
- Produces: each evidence item carries `receipt_id`, `chunk_id`, `document_name`, `start_offset`, `end_offset` alongside its existing fields. `DisclosureEvidence` gains the same five.

- [ ] **Step 1: Write the failing tests**

```python
# services/disclosure-retrieval/tests/test_disclosures.py
def test_evidence_identifies_its_receipt_document_and_character_range(client, indexed_chunk):
    item = client.post(SEARCH_URL, json={"query": "위험"}).json()["latest_evidence"][0]
    assert item["receipt_id"] == "20260814000001"
    assert item["chunk_id"] == indexed_chunk["id"]
    assert item["document_name"] == "00001.xml"
    assert item["start_offset"] == 0
    assert item["end_offset"] == len(item["text"])
```

```python
# services/portfolio-builder/tests/test_retrieval_client.py
def test_evidence_keeps_its_source_location(...):
    # DisclosureSearchResult.model_validate(payload) preserves all five fields
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-retrieval/tests/test_disclosures.py -k identifies -v`
Expected: FAIL with `KeyError: 'receipt_id'`.

- [ ] **Step 3: Select and return the five fields**

Add `c.document_name`, `c.start_offset`, `c.end_offset` to the `filtered_chunks` CTE and carry them through `candidates`, then add all five to the output mapping. Add the five to `DisclosureEvidence` — `chunk_id: int`, offsets `int`, the rest `str` — and document in the field descriptions that offsets are half-open Unicode character positions within `document_name`.

- [ ] **Step 4: Confirm the tests pass**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-retrieval services/portfolio-builder -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/disclosure-retrieval services/portfolio-builder
git commit -m "feat: return receipt document and character range with each evidence item"
```

---

### Task 5: Keep chunk groups inside one document

**Files:**
- Modify: `services/disclosure-indexer/src/disclosure_indexer/chunks.py`
- Modify: `services/disclosure-indexer/src/disclosure_indexer/index.py:112-148`
- Test: `services/disclosure-indexer/tests/test_chunks.py`, `services/disclosure-indexer/tests/test_index.py`

**Interfaces:**
- Produces: `chunk_documents(documents: list[dict[str, Any]], max_bytes: int, overlap_bytes: int) -> list[list[dict[str, Any]]]` in `chunks.py` — one inner list per document, in archive order, documents with no sections omitted. `chunk_sections` keeps its signature and stays the per-document worker.
- `CHUNK_VERSION` becomes `"documents-byte-budget-v1"`, which changes every index signature and so forces a re-index. That is intended.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chunks.py
def test_chunk_documents_groups_by_document_in_archive_order():
    groups = chunk_documents(two_file_archive, 2400, 300)
    assert [g[0]["document"] for g in groups] == ["00001.xml", "00002.xml"]
    assert all(len({c["document"] for c in g}) == 1 for g in groups)
```

```python
# tests/test_index.py
def test_embedding_batches_never_span_two_documents(pg_engine, settings, recorded_embed):
    # settings.batch_size larger than either document's chunk count
    index_documents(pg_engine, settings)
    for call in recorded_embed.calls:
        assert len({text_document(t) for t in call}) == 1
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest services/disclosure-indexer/tests/test_chunks.py -k chunk_documents -v`
Expected: FAIL with `NameError: chunk_documents`.

- [ ] **Step 3: Implement `chunk_documents` and batch within a group**

In `index_documents`, replace the flattening comprehension with `chunk_documents(row["documents"], ...)`, then iterate groups and slice each group into `batch_size` batches. Flatten the groups in order for the ordinal numbering and the `zip` against vectors, so stored ordinals stay sequential across the whole receipt.

- [ ] **Step 4: Confirm the tests pass and the rest still does**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/disclosure-indexer
git commit -m "feat: batch disclosure chunks per document instead of per receipt"
```

---

### Task 6: Bound indexing retries and pin the query instruction

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0013_disclosure_index_attempts.py`
- Modify: `services/disclosure-indexer/src/disclosure_indexer/index.py`, `settings.py`
- Test: `services/disclosure-indexer/tests/test_index.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: Task 1's head, `0012`.
- Produces: `disclosure_indexes.attempts INTEGER NOT NULL DEFAULT 0` and `dead_letter` in its status check. `Settings.query_instruction`, read from `DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION`, defaulting to the same string `disclosure-retrieval` uses, and included in the signature config under key `query_instruction`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_index.py
def test_a_document_that_always_fails_reaches_dead_letter(pg_engine, settings, failing_embed):
    for _ in range(settings.max_attempts):
        index_documents(pg_engine, settings)
    assert index_status(pg_engine) == "dead_letter"


def test_dead_letter_documents_make_no_further_embedding_calls(pg_engine, settings, failing_embed):
    # after dead_letter, one more run
    assert failing_embed.calls == 0


# tests/test_settings.py
def test_the_signature_changes_with_the_query_instruction(monkeypatch):
    # two Settings differing only in DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION
    assert first != second
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer -k "dead_letter or query_instruction" -v`
Expected: FAIL — `attempts` column absent, signature unchanged.

- [ ] **Step 3: Write migration `0013`, revision `0013`, down `0012`**

Add the `attempts` column with a `BETWEEN 0 AND 5` check, replace the status check to include `dead_letter`, and extend `disclosure_indexes_work_idx` to lead with `signature, status`. `downgrade` reverses all three.

- [ ] **Step 4: Bound the claim and record the failure**

In the claim query add `AND i.attempts < :max_attempts`. In the failure handler set `attempts = LEAST(attempts + 1, :max)` and `status = CASE WHEN attempts + 1 >= :max THEN 'dead_letter' ELSE 'failed' END`, mirroring `collect.py`. On success reset `attempts = 0`. Add `query_instruction` to `Settings` and to the signature `config` dict.

- [ ] **Step 5: Confirm the tests pass**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer -v` and `uv run alembic upgrade head && uv run alembic downgrade 0012`
Expected: PASS, migration reversible.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/postgres services/disclosure-indexer
git commit -m "feat: bound disclosure indexing attempts and sign the query instruction"
```

---

### Task 7: Reject an embedding input that cannot carry its header

**Files:**
- Modify: `services/disclosure-indexer/src/disclosure_indexer/index.py:17-33`, `settings.py:43-48`
- Test: `services/disclosure-indexer/tests/test_index.py`, `tests/test_settings.py`

**Interfaces:**
- Produces: `embedding_inputs` raises `ValueError("embedding input cannot carry its metadata header")` when the full header does not fit. The settings validator requires `input_bytes >= chunk_bytes + 600`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_index.py
def test_embedding_input_rejects_a_budget_that_truncates_the_header():
    with pytest.raises(ValueError, match="metadata header"):
        embedding_inputs(report, [long_chunk], 9010)


def test_embedding_input_keeps_the_whole_header_when_it_fits():
    assert embedding_inputs(report, [long_chunk], 12000)[0].startswith("삼성전자 (005930) 2026 half")
```

```python
# tests/test_settings.py
def test_input_budget_must_leave_room_for_the_header(monkeypatch):
    # DISCLOSURE_INDEXER_CHUNK_BYTES=2400, DISCLOSURE_INDEXER_INPUT_BYTES=2500
    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest services/disclosure-indexer -k "header or input_budget" -v`
Expected: FAIL — truncates silently, settings accepted.

- [ ] **Step 3: Replace the slice with a check**

Drop the `errors="ignore"` byte slice. Raise when the encoded header exceeds `remaining`. Raise the validator's margin from 100 to 600 bytes.

- [ ] **Step 4: Confirm the tests pass**

Run: `uv run pytest services/disclosure-indexer -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/disclosure-indexer
git commit -m "fix: reject an embedding budget that would truncate the metadata header"
```

---

### Task 8: Report a space mismatch as data and count the candidates

**Files:**
- Modify: `services/disclosure-retrieval/src/disclosure_retrieval/repository.py:57-160`, `settings.py:35`
- Modify: `services/portfolio-builder/src/portfolio_builder/disclosures.py`
- Test: `services/disclosure-retrieval/tests/test_disclosures.py`

**Interfaces:**
- Produces: `report_rows` adds `index_state`, one of `ready`, `pending`, `other_space`. `search_reports` no longer raises on mismatch and returns `searched` and `returned` counts per scope as `latest_searched`, `latest_returned`, `history_searched`, `history_returned`. `Settings.max_distance` becomes `float | None` defaulting to `None`.
- `DisclosureReport.index_status` is replaced by `index_state: Literal["ready", "pending", "other_space"]`; `DisclosureSearchResult` gains the four counts.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_partial_reindex_returns_the_matching_evidence(client, two_space_fixture):
    body = client.post(SEARCH_URL, json={"query": "위험"}).json()
    assert body["latest_evidence"], "evidence from the configured space must survive"
    assert {r["index_state"] for r in body["reports"]} == {"ready", "other_space"}


def test_an_empty_result_reports_how_many_candidates_were_searched(client, indexed_chunk):
    body = client.post(SEARCH_URL, json={"query": "관련없는질의", "max_distance": 0.0}).json()
    assert body["latest_evidence"] == []
    assert body["latest_searched"] == 1


def test_no_threshold_is_applied_by_default(client, distant_chunk):
    assert client.post(SEARCH_URL, json={"query": "위험"}).json()["latest_evidence"]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-retrieval -k "partial_reindex or candidates_were_searched or no_threshold" -v`
Expected: FAIL — 503 on mismatch, no counts, 0.5 cutoff applied.

- [ ] **Step 3: Replace the raise with a per-report state**

In the `reports` CTE derive `index_state` by comparing `i.model` and `i.dimensions` against the bound `:model` and `:dimensions`. Delete the `DISTINCT` pre-query and the `raise`. Keep `AND r.index_model=:model AND r.index_dimensions=:dimensions` on `filtered_chunks` so only the configured space is searched. Count `filtered_chunks` rows for `searched` and the returned rows for `returned`. Apply `max_distance` only when it is not `None`. Add `max_distance: float | None` to `SearchRequest` so a caller can ask for one per request, bounded `ge=0, le=2`.

- [ ] **Step 4: Update the consumer model and confirm**

Rename `index_status` to `index_state` with the three-value literal in `DisclosureReport` and add the four counts to `DisclosureSearchResult`. Update `PUBLIC_FIELDS`.

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-retrieval services/portfolio-builder -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/disclosure-retrieval services/portfolio-builder
git commit -m "feat: report index space mismatch as data and count searched candidates"
```

---

### Task 9: Close the collection gaps

**Files:**
- Modify: `services/disclosure-indexer/src/disclosure_indexer/collect.py`, `settings.py`
- Test: `services/disclosure-indexer/tests/test_collect.py`

**Interfaces:**
- Produces: `Settings.recheck_days` (default 400) replaces the literal 7 in the progress skip; `Settings.refresh_days` (default 30) bounds the receipts re-fetched for a hash comparison. `collect` returns the existing counters plus `unclassified` and `replaced`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_replaced_source_file_is_collected_again(pg_engine, settings, dart_returning_new_bytes):
    # a ready receipt published inside refresh_days whose DART bytes now hash differently
    assert collect(...)["replaced"] == 1
    assert document_versions(pg_engine, receipt) == 2


def test_a_correction_filed_long_after_its_window_is_still_collected(pg_engine, settings):
    # a complete progress row whose end_date is 90 days old, recheck_days=400
    assert collect(...)["ready"] == 1


def test_an_unclassifiable_report_title_is_counted(pg_engine, settings, dart_with_changed_closing_month):
    assert collect(...)["unclassified"] == 1


def test_a_failing_window_does_not_skip_the_remaining_windows(pg_engine, settings, dart_failing_second_window):
    assert progress_rows(pg_engine) == expected_window_count - 1


def test_reconciliation_stops_at_the_document_limit(pg_engine, settings_with_limit_1):
    assert collect(...)["failed"] + collect(...)["ready"] <= 1


def test_a_settled_unavailable_document_is_not_reparsed(pg_engine, settings, counting_parser):
    collect(...); collect(...)
    assert counting_parser.calls == 1
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer/tests/test_collect.py -v`
Expected: six failures.

- [ ] **Step 3: Implement the six changes in `collect.py`**

Reconciliation pass: add `LIMIT :document_limit` ordered by `r.updated_at`, and extend the skip condition so a document whose `parser_version` is current and whose `extraction_status` is `ready` *or* `unavailable` is left alone when its archive is present.

Refresh pass: for receipts whose `published_date >= today - refresh_days` and whose status is `ready` or `unavailable`, fetch `document.xml`, compare the SHA-256 against the newest stored `source_hash`, and call `store_document` only when it differs, counting `replaced`.

Progress skip: `finish < today - timedelta(days=settings.recheck_days)`.

Title classification: count `unclassified` and `log.warning("disclosure_unclassified", ...)` in the `except ValueError` branch instead of a bare `continue`.

Window boundary: move the `try`/`except` from around the window loop to around one window's body, so one failure costs one window.

- [ ] **Step 4: Confirm the tests pass**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/disclosure-indexer
git commit -m "feat: detect replaced sources and stop losing late corrections"
```

---

### Task 10: Store and serve document sections

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0014_disclosure_sections.py`
- Create: `services/disclosure-indexer/src/disclosure_indexer/sections.py`
- Create: `services/disclosure-retrieval/src/disclosure_retrieval/reads.py`
- Modify: `services/disclosure-indexer/src/disclosure_indexer/collect.py` (`store_document`), `services/disclosure-retrieval/src/disclosure_retrieval/routes.py`
- Test: `services/disclosure-indexer/tests/test_sections.py`, `services/disclosure-retrieval/tests/test_reads.py`

**Interfaces:**
- Consumes: Task 6's head, `0013`.
- Produces: `section_rows(documents: list[dict[str, Any]]) -> list[dict[str, Any]]` in `sections.py`, keys `document_name`, `document_order`, `ordinal`, `heading`, `topic`, `text`, `start_offset`, `end_offset`.
- `read_sections(conn, stock_code: str, receipt_id: str, *, document_name: str | None, topic: str | None, heading: str | None, limit: int) -> list[dict[str, Any]]` in `reads.py`.
- `GET /stocks/{stock_code}/disclosures/sections` with required `receipt_id`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sections.py
def test_section_rows_number_offsets_per_document():
    rows = section_rows(two_file_archive)
    first = [r for r in rows if r["document_name"] == "00001.xml"]
    second = [r for r in rows if r["document_name"] == "00002.xml"]
    assert first[0]["start_offset"] == 0 and second[0]["start_offset"] == 0
    assert [r["ordinal"] for r in first] == list(range(len(first)))
```

```python
# tests/test_reads.py
def test_sections_are_readable_with_no_embedding_configuration(client_without_embedding_env, stored_sections):
    body = client_without_embedding_env.get(SECTIONS_URL, params={"receipt_id": RECEIPT}).json()
    assert body["sections"][0]["heading"] == "II. 사업의 내용"
    assert body["sections"][0]["receipt_id"] == RECEIPT


def test_sections_require_an_explicit_receipt(client):
    assert client.get(SECTIONS_URL).status_code == 422


def test_sections_survive_an_embedding_failure(pg_engine, settings, failing_embed):
    collect(...); index_documents(pg_engine, settings)
    assert stored_section_count(pg_engine) > 0
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer/tests/test_sections.py services/disclosure-retrieval/tests/test_reads.py -v`
Expected: FAIL — module and table absent.

- [ ] **Step 3: Write migration `0014`, revision `0014`, down `0013`**

`disclosure_sections` per spec §4.1, with a `document_id` foreign key to `disclosure_documents` cascading on delete, `UNIQUE(document_id, document_name, ordinal)`, and an index on `(receipt_id, document_name)`.

- [ ] **Step 4: Write the rows and the read**

Implement `section_rows`. In `store_document`, insert the rows in the same transaction that sets `extraction_status`, deleting any rows for that `document_id` first. Implement `read_sections` with every filter bound and `limit` capped at 200, and add the route. The route must not construct `Settings` or call `embed_query`.

- [ ] **Step 5: Confirm the tests pass**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer services/disclosure-retrieval -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/postgres services/disclosure-indexer services/disclosure-retrieval
git commit -m "feat: store disclosure sections and read them without embeddings"
```

---

### Task 11: Store and serve structured financial facts

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0015_disclosure_financial_facts.py`
- Create: `services/disclosure-indexer/src/disclosure_indexer/financials.py`
- Modify: `services/disclosure-indexer/src/disclosure_indexer/dart.py`, `collect.py`, `settings.py`
- Modify: `services/disclosure-retrieval/src/disclosure_retrieval/reads.py`, `routes.py`
- Test: `services/disclosure-indexer/tests/test_financials.py`, `services/disclosure-retrieval/tests/test_reads.py`

**Interfaces:**
- Consumes: Task 10's head, `0014`; `reads.py` from Task 10.
- Produces: `DartClient.financial_statements(corp_code: str, year: int, kind: str, scope: str) -> list[dict[str, Any]]` calling `fnlttSinglAcntAll.json`, mapping `kind` to OpenDART's `reprt_code` (`q1`→`11013`, `half`→`11012`, `q3`→`11014`, `annual`→`11011`) and `scope` to `fs_div`.
- `financial_rows(payload: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]` in `financials.py`, keys `scope`, `statement_type`, `account_id`, `account_name`, `currency`, `ordinal`, `source_value`, `value`, `normalized`.
- `read_financials(conn, stock_code: str, *, period_end: date | None, business_year: int | None, scope: str | None, statement_type: str | None, account_id: str | None, limit: int) -> list[dict[str, Any]]`.
- `GET /stocks/{stock_code}/disclosures/financials`, requiring `period_end` or `business_year`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_financials.py
def test_an_unparseable_value_keeps_its_source_string():
    row = financial_rows([{"thstrm_amount": "-", "account_id": "ifrs-full_Assets", ...}], "CFS")[0]
    assert row["source_value"] == "-" and row["value"] is None and row["normalized"] is False


def test_a_negative_parenthesised_amount_normalises():
    assert financial_rows([{"thstrm_amount": "(1,234)", ...}], "CFS")[0]["value"] == -1234


def test_a_missing_amount_is_null_not_zero():
    assert financial_rows([{"thstrm_amount": "", ...}], "CFS")[0]["value"] is None


def test_the_same_account_is_kept_once_per_scope(pg_engine, dart_with_both_scopes):
    assert scopes_for(pg_engine, "ifrs-full_Assets") == {"CFS", "OFS"}
```

```python
# tests/test_reads.py
def test_financials_are_readable_with_no_embedding_configuration(client_without_embedding_env, stored_facts):
    body = client_without_embedding_env.get(FIN_URL, params={"business_year": 2026, "scope": "CFS"}).json()
    assert body["facts"][0]["account_id"] == "ifrs-full_Assets"
    assert body["facts"][0]["receipt_id"] == RECEIPT


def test_financials_require_an_explicit_period(client):
    assert client.get(FIN_URL).status_code == 422


def test_financials_never_merge_unspecified_scopes(client, stored_facts_both_scopes):
    body = client.get(FIN_URL, params={"business_year": 2026}).json()
    assert {f["scope"] for f in body["facts"]} == {"CFS", "OFS"}
    assert all("scope" in f for f in body["facts"])
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer/tests/test_financials.py services/disclosure-retrieval/tests/test_reads.py -k financ -v`
Expected: FAIL — module, table, and route absent.

- [ ] **Step 3: Write migration `0015`, revision `0015`, down `0014`**

`disclosure_financial_facts` per spec §4.2, `receipt_id` cascading from `disclosures`, `value` as `NUMERIC` nullable, `normalized` boolean not null, `UNIQUE(receipt_id, scope, statement_type, account_id, ordinal)`, an index on `(stock_code, business_year, scope)`, and a check that `scope IN ('CFS','OFS')`.

- [ ] **Step 4: Fetch, normalise, store, and read**

Add `financial_statements` to `DartClient`, reusing its interval and attempt handling and treating status `013` as an empty list. Implement `financial_rows`: strip thousands separators, read a leading `-` or a parenthesised amount as negative, and set `normalized` false with `value` null for anything else, keeping `source_value` verbatim. In `collect`, after a receipt reaches `ready`, fetch both scopes and replace that receipt's rows in one transaction; count a fetch failure as `failed` without changing the receipt's own status, since the document is already usable. Add `Settings.collect_financials: bool = True` so the fetch can be disabled. Implement `read_financials` and the route, both bound, `limit` capped at 500.

- [ ] **Step 5: Confirm the tests pass**

Run: `KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest services/disclosure-indexer services/disclosure-retrieval -v` and `uv run alembic upgrade head && uv run alembic downgrade 0010 && uv run alembic upgrade head`
Expected: PASS, migrations reversible end to end.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/postgres services/disclosure-indexer services/disclosure-retrieval
git commit -m "feat: store OpenDART financial facts and read them without embeddings"
```

---

### Task 12: Configuration, documentation, and whole-branch verification

**Files:**
- Modify: `.env.example`, `AGENTS.md`, `README.md`, `compose.local.yaml`, `compose.prod.yaml`
- Modify: `docker/requirements/disclosure-indexer.txt`, `docker/requirements/disclosure-retrieval.txt`, `docker/requirements/app.txt`

- [ ] **Step 1: Add every new setting to `.env.example` and the `AGENTS.md` table**

`DISCLOSURE_INDEXER_RECHECK_DAYS`, `DISCLOSURE_INDEXER_REFRESH_DAYS`, `DISCLOSURE_INDEXER_COLLECT_FINANCIALS`, and the now-nullable `DISCLOSURE_RETRIEVAL_MAX_DISTANCE`, each with the default the code uses. Correct the `DISCLOSURE_EMBEDDING_DIMENSIONS` row, which says `halfvec(2000)` index but is documented against the wrong table. Document the two new endpoints and that both are GPU-free.

- [ ] **Step 2: Pass both into Compose**

Add the three indexer settings to `compose.local.yaml` and `compose.prod.yaml` with the same defaults.

- [ ] **Step 3: Refresh the exported requirements**

Run the repository's `uv export` commands for `disclosure-indexer`, `disclosure-retrieval`, and `app`, then `git diff --exit-code -- docker/requirements/`.
Expected: no diff after regeneration, or a diff that only reflects a deliberate dependency change.

- [ ] **Step 4: Run every check**

```bash
uv run ruff check . && uv run ruff format --check .
uv run tach check && uv run tach check-external -e services
uv run deptry services/disclosure-indexer/src --config services/disclosure-indexer/pyproject.toml
uv run deptry services/disclosure-retrieval/src --config services/disclosure-retrieval/pyproject.toml
KTB_POSTGRES_DSN=<dsn> uv run alembic upgrade head
KTB_TEST_POSTGRES_DSN=<dsn> uv run pytest
docker compose -f compose.local.yaml config >/dev/null
docker compose -f compose.prod.yaml config >/dev/null
docker build -f docker/disclosure-indexer.Dockerfile .
docker build -f docker/disclosure-retrieval.Dockerfile .
```

Expected: all pass, no disclosure test skipped, both images build. Report anything that does not, separating a pre-existing failure from one this branch introduced.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: document disclosure settings and the GPU-free read endpoints"
```

## Open Items

- The renumbering in Task 1 assumes no deployed database applied a migration named `0011_create_disclosures`. Confirm against the deployed `alembic_version` before release.
- `CHUNK_VERSION` changes in Task 5, so every index signature changes and a full re-index is required. Expected, and cheap today because nothing is indexed in production yet.
- Serving two embedding spaces at once still needs `disclosure_indexes_active_idx` relaxed. That belongs to the 1024-dimensional migration, not here.
- The reranker, the Context checkpoint, the INT8 transformation, and the hosting decision remain unimplemented and keep their contracts in the serving design.
