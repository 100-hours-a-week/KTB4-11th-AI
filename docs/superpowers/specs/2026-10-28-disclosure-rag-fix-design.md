# Disclosure Indexing and Retrieval Defect Fix Design

Date: 2026-10-28

Status: Baseline verified by execution against a dedicated PostgreSQL 18 + pgvector 0.8.6 container. Defects below are confirmed, not inferred, except where marked. Model selection, GPU serving, and reranking stay out of scope and keep the contracts in `2026-10-28-disclosure-rag-serving-design.md`.

## 1. Scope

Issue #346 asks for two things this document covers: the periodic-disclosure services must actually run, and structured financial values and document sections must be readable without a GPU. It also asks for a fine-tuned 1024-dimensional Context model served on RunPod or Modal with a Qwen reranker; that work needs a checkpoint and a provider that do not exist yet, so this document leaves the 2000-dimensional vector contract in place and only removes the obstacles to replacing it later.

In scope:

- Every defect in §3, which are failures of the code as written.
- `disclosure_financial_facts` and `disclosure_sections` with GPU-free read endpoints.

Out of scope, unchanged:

- The embedding model, its dimensions, the INT8 output transformation, and the adapter identity columns.
- The reranker and the top-50 candidate pool.
- GPU runtime code and hosting selection.

## 2. Verified Baseline

Branch `feat/346-disclosure-rag`, cut from `dev` (641a1ab), carrying the issue #346 work that was uncommitted in the root checkout.

- `pytest`: 843 passed, 241 skipped. Every skip is a database test waiting on `KTB_TEST_POSTGRES_DSN`.
- `alembic upgrade head`: **fails** — `Multiple head revisions are present for given argument 'head'`.
- `ruff check`, `ruff format --check`, `tach check`, `tach check-external -e services`, `deptry`, and the `uv export` drift check all pass.

Local environments need one unrelated repair. uv writes workspace `.pth` files with the macOS `UF_HIDDEN` flag set, and Python 3.13 silently skips hidden `.pth` files, so no workspace package imports and the whole suite fails to collect. `chflags nohidden .venv/lib/python3.13/site-packages/*.pth` after every `uv sync` restores it. Linux CI is unaffected because BSD file flags do not exist there.

Test databases are dedicated and disposable. Never point `KTB_TEST_POSTGRES_DSN` at a deployed database.

## 3. Defects

### 3.1 Blocking

**D1. Migration revision `0011` is defined twice.** `0011_create_disclosures.py` and the already-committed `0011_add_corporation_sector.py` both declare `revision = "0011"` and `down_revision = "0010"`. Alembic reports two heads and refuses `upgrade head`, so the schema cannot be created at all and every database test stays skipped.

The disclosure migration has never been committed and no deployed database can have applied it, so renumbering it is safe: it becomes revision `0012` with `down_revision = "0011"`. If a deployed database turns out to have applied a migration named `0011_create_disclosures`, that database needs a manual `alembic stamp` instead, and this assumption must be checked against the deployed revision before release.

**D2. The dimension probe subtracts four from a pgvector typmod.** `index.py` reads `atttypmod - 4`. pgvector stores the dimension in `atttypmod` directly; the `- 4` convention belongs to `varchar`, whose typmod carries `VARHDRSZ`. Measured against the real extension:

```
halfvec(2000) → atttypmod = 2000, atttypmod - 4 = 1996
```

So `index_documents` raises `disclosure embedding dimension mismatch: configured=2000, database=1996` before processing anything. The repository already has the robust pattern in `infrastructure/postgres/tests/test_migrations.py`: `format_type(atttypid, atttypmod)`.

**D3. Production Compose gives neither service a database DSN.** Both inherit only the `x-postgres-environment` anchor, which names the other ten services. `postgres_dsn` is required, so both containers fail settings validation at startup and portfolio-builder never starts behind its retrieval dependency. The local Compose file already sets both.

### 3.2 Evidence and correctness

**D4. Search results omit the evidence location.** The response carries `title`, period, `section`, `topic`, `text`, `context`, and `score`. It drops `receipt_id` and `chunk_id`, which the query already selects, and never selects `document_name`, `start_offset`, or `end_offset`, which the table stores. Issue #346 requires that a result identify the original disclosure and the position of the evidence.

Offsets alone are ambiguous: `parse_document` restarts character offsets at zero for each file inside a receipt's archive, so an offset is meaningless without `document_name`.

**D5. Chunk batches mix different physical documents.** `index.py` flattens `document["sections"]` across every file in the archive into one list, then slices it into fixed-size batches. Each chunk keeps its `document` field, so storage is correct today, but the batch boundary ignores file boundaries. The serving design forbids grouping chunks across documents, and the grouped-encoding contract it specifies cannot be built on batches drawn this way.

Chunking must iterate per document, and a batch must never span two documents.

**D6. One mismatched index fails the whole stock.** When any report in scope carries an active index built with a different model or dimension, retrieval raises and returns 503 for the entire request. Changing `DISCLOSURE_EMBEDDING_MODEL` therefore takes every search for that stock down until every document is re-indexed, and the response is indistinguishable from an upstream outage.

The candidate query already filters on `index_model`. Retrieval must return the evidence that does match the configured space and report the mismatch as data, not as a failure: each report gains an `index_state` of `ready`, `pending`, or `other_space`.

Serving two spaces at once additionally requires relaxing `disclosure_indexes_active_idx`, which permits one active index per document. That belongs to the model migration and stays out of scope; this change only stops a partial re-index from causing an outage.

**D7. A normal empty result is indistinguishable from a threshold rejection.** `max_distance` defaults to 0.5 and silently removes candidates, and the response reports no counts. The serving design forbids carrying 0.5 forward unvalidated and forbids a default threshold on the new path.

The default becomes no threshold. `max_distance` stays configurable and, when set, the response reports `searched` and `returned` counts per scope so a caller can tell an empty result caused by filtering from an absence of evidence.

**D8. Embedding input silently loses its metadata header.** The chunk body is reserved first and the header is byte-sliced into whatever remains with `errors="ignore"`. The settings validator accepts `input_bytes = chunk_bytes + 100`, which truncates a Korean header to a few characters:

```
budget 9010 → '삼성전\n가가가…'
budget 9100 → '삼성전자 (005930) 2026 half 2026-06-30\nII. 사업의 내용\n…'
```

Nothing records the loss. A header that does not fit is a configuration error and must be rejected, not silently cut.

### 3.3 Collection gaps

**D9. A replaced source file is never detected.** Collection skips any receipt whose status is `ready`, `unavailable`, or `dead_letter`, and the reconciliation pass reuses the local archive whenever the file is present. No path compares a fresh DART response against the stored `source_hash`, so a disclosure re-published under the same receipt number is never re-indexed. `disclosure_documents` is already keyed on `(receipt_id, source_hash, parser_version)` and will accept the new version once something fetches it.

Recently published receipts must be re-fetched and hash-compared, bounded by a configurable window so the daily run does not re-download the archive.

**D10. Completed windows are never re-examined after seven days.** A correction filed more than a week after its window closes is never collected. The re-check window must be configurable and default wide enough to cover a correction season.

**D11. Unparseable report titles vanish without a trace.** `report_period` raises for a title whose period does not match the company's current closing month, and the caller swallows it with `continue`. The closing month is re-fetched on every run, so a company that changes its fiscal year silently stops yielding history, and no counter moves. These must be counted and logged.

**D12. A failure in one window skips that company's remaining windows.** The `try` wraps the whole window loop. A transient error in an early window costs every later window in that run. The boundary belongs around one window.

**D13. Reconciliation is unbounded and re-parses settled documents forever.** The pass selects every disclosure on every run with no limit, and a document that legitimately parses to `unavailable` is re-parsed indefinitely because its `extraction_status` never becomes `ready`. It needs the same `document_limit` bound the indexing loop has, and it must not re-parse a document whose current parser already produced its final state.

**D14. Indexing retries are unbounded.** `disclosures` has `attempts`, `max_attempts`, and `dead_letter`, but `disclosure_indexes` has no attempt counter, so a document that always fails to embed is retried on every run forever. This contradicts the acceptance criterion that no eligible work means no GPU calls. `disclosure_indexes` gains `attempts`, bounded by the same `max_attempts`, and a `dead_letter` status.

**D15. The index signature ignores the query instruction.** The signature hashes the chunker, input format, byte budgets, model, and dimensions. `DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION` is not part of it, so changing the query prefix changes the query vector space while every stored index still matches. The instruction belongs in the signature, which means the indexer must read it.

**D16. `/health` never touches the database.** It returns `ok` while PostgreSQL is unreachable, so Compose reports the service healthy and portfolio-builder starts behind a dependency that cannot answer.

## 4. Structured Facts and Sections

Both reads are pure PostgreSQL and must work when no embedding index exists and no GPU is reachable.

### 4.1 `disclosure_sections`

The parser already produces sections; they are only stored inside `disclosure_documents.documents` as JSONB. Promote them to rows so a caller can read an identified part of a report directly.

Columns: receipt, document name, document order, section ordinal, heading, topic, text, start and end character offsets, parser version. Keyed on `(receipt_id, document_name, ordinal)`. Offsets are Unicode character positions in that document's parsed text, half-open `[start, end)`.

Rows are written in the same transaction that marks a document's extraction `ready`, and replaced wholesale when a new `disclosure_documents` row supersedes it. Section rows must survive embedding failure.

### 4.2 `disclosure_financial_facts`

Source is OpenDART's `fnlttSinglAcntAll.json`, requested per receipt for the report's fiscal year and division. It is a separate request from the document archive and must respect the same interval and attempt limits.

Columns: receipt, stock code, business year, report kind, period end, consolidation scope (`CFS` or `OFS`), statement type, account id, account name, currency, the source value string exactly as returned, and a normalized `NUMERIC` value that is null when the string does not parse. Keyed on `(receipt_id, scope, statement_type, account_id, ordinal)`.

A missing value is null, never zero. A value that fails to normalize keeps its source string and sets a `normalized` flag false. Account names are never merged across periods, scopes, currencies, or units.

### 4.3 Endpoints

`GET /stocks/{stock_code}/disclosures/financials` requires an explicit `period_end` or `business_year`, and accepts `scope`, `statement_type`, and `account_id`. `GET /stocks/{stock_code}/disclosures/sections` requires an explicit `receipt_id`, and accepts `document_name`, `topic`, and a heading substring. Both bound every SQL parameter, cap returned rows, and expose no arbitrary SQL. Neither merges unspecified scopes.

Both responses carry the receipt, period, scope, parser version, and offsets so a caller can locate the source.

## 5. Acceptance

1. `alembic upgrade head` applies cleanly to an empty database, and `downgrade` reverses it.
2. `pytest` runs with `KTB_TEST_POSTGRES_DSN` set and no disclosure test skips.
3. `index_documents` accepts a `halfvec(2000)` column and rejects a column of any other width, proven against the real extension.
4. A search result identifies its receipt, chunk, document, and character range.
5. A partial re-index returns the matching evidence and reports the rest as `other_space`, never 503.
6. An empty result reports how many candidates were searched.
7. A document that always fails reaches `dead_letter` and stops consuming embedding calls.
8. Financial values and sections are readable with no embedding configuration present.
9. `ruff`, `tach`, `deptry`, the requirements drift check, and both image builds pass.
