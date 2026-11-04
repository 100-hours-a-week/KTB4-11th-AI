# Disclosure services review follow-up

Date: 2026-10-30

## Scope and decisions

PR #432 contains DB indexing, disclosure retrieval and portfolio-builder tools.
Collection PR #441 is merged into dev. This branch uses the current dev conventions
for Python, CI, packaging and service configuration. GPU deployment belongs to #430.
The root checkout and running services are not changed.

Disclosure processing is disabled by default through `DISCLOSURE_ENABLED=false`.
Portfolio-builder calls the `services/disclosure-retrieval` Python package directly when enabled.
Its tools receive the builder's SQLAlchemy engine and component model settings.
There is no separate retrieval HTTP server, controller, router, client, port,
healthcheck, console command, or Docker container. The retrieval package declares no FastAPI
or uvicorn dependency. Existing news/market HTTP and backend polling are unchanged.

Retrieval returns source evidence, never a generated answer. Financial and section
reads use PostgreSQL only. Narrative search embeds the query, retrieves candidates,
and reranks them. The Modal reranker uses the yes/no logit difference and does not
call `generate()`. Portfolio-builder's existing portfolio and explanation generation
is unchanged.

## Implementation

1. Read model repositories, revisions, adapter hashes, dimensions, and token budget
   through `os.environ.get` in `ktb_core.embedding.config`. Preserve the evaluated
   defaults and the model-identity and index-space checks. Fixed quantization,
   normalization, and prompt-version fields remain constants because environment
   labels cannot change their implementation. Share model overrides between the
   indexer and builder containers through Compose environment mappings.
2. Use the existing `opendart/opendart.py` API publisher for short and long filings.
   Keep official long filing codes in `disclosures.detail_type`; the ordinary
   publisher fetches and parses each long filing before standard scraping stores its
   metadata, documents, sections and financial accounts in one transaction. The modules
   are flat under `opendart/`; no staged queue or second collection pass remains.
   Long filings are not embedded during ingestion. A filing type is separate from the content split. Nonperiodic
   filings retain unknown fiscal fields as null, and their tables remain sections.
   Collection migrations 0013–0015 are already in dev; indexing adds only 0016.
   Collection uses the existing OpenDART API path and its persisted daily state.
   The existing hourly news-preprocessor service starts the DB-only indexer after
   success or final failure. The indexer never fetches RSS, DART or source archives.
3. Put portfolio-builder's disclosure tools in `tools/disclosure/search.py`,
   `get.py`, and `tool.py`. Expose `search_disclosures`, `get_disclosure_sections`,
   and `get_disclosure_financials`. Require an explicit year or period for financial
   reads. Execute parameterized SQL in-process using the existing engine. Search
   orchestrates DB reads and remote model calls through the retrieval functions. Query and
   reranking inference run after the DB connection has returned to its pool.
   Accept an optional `detail_type` search filter and return that code with evidence.
   Unknown-period receipts stay separate; periodic corrections retain their existing
   grouping. Existing periodic embedding inputs and model identity stay unchanged.
4. Remove `portfolio_builder/disclosures.py` and `retrieval_client.py`. Reuse the
   retrieval code's typed `SearchResult` in the submission hook through Pydantic TypeAdapter;
   its three result lists are required, so malformed `{}` cannot count as a search.
   When enabled, a successful narrative search is required before submission.
   When disabled, disclosure tools, their prompt instructions and their submission
   middleware are absent; technical analysis remains required.
5. Receive an optional investment profile in the existing backend polling response.
   Do not add an endpoint, polling loop, model call, or trading rule for this field.

Only external model inference uses HTTPS. Financial and section reads make no HTTP
or GPU call. The whole-service performance benchmark remains subsequent work;
removing HTTP is not itself a measured latency or throughput improvement.

Model overrides must match the GPU serving checkpoint and identity. Changing a CPU
environment variable does not deploy different GPU weights. Incompatible serving
identities are rejected. Changing embedding metadata creates a different index space;
old vectors are not silently reused.

## Accuracy and failed-index recovery

Modal indexes every nonblank narrative section, including `topic=other`. A topic is
metadata, not an eligibility filter. Asset-disposal text from the real OpenDART parser
is covered by a regression test. The `content-kind-v2` signature creates new indexing
work for documents previously indexed under the narrower topic rule.

A document containing no narrative text finishes with `status=empty`, `active=false`.
It creates no vectors, makes no inference call, and is not reported as ready. Retrieval
returns its `empty` state and no narrative candidates. Repeating the job does not retry
these documents. Migration 0016 includes this state; collection revisions are unchanged.

Financial reads select the latest stored filing in each company/year/report-type/period
family before filtering accounts and consolidation scope. The family normalizes legacy
periodic detail codes. An unavailable, failed, or not-yet-parsed latest filing does not
silently fall back to an older amount. Results include the filing title, publication
date and `previous_receipt_id`. This field identifies the nearest earlier stored filing
in the same family, not an official DART correction link. An uncollected predecessor
cannot be identified. Independent companies, periods, and report families remain separate.

Normal indexing preserves the configured failure limit and does not automatically revive
`dead_letter` work. After fixing an inference outage, explicitly retry named receipts:

```bash
uv run disclosure-indexer --retry-receipt RECEIPT_ID
```

Repeat the option for multiple receipts. This resets only dead-letter indexes for the
current signature and latest parsed version of each named receipt. The run processes
only the named receipts, leaving unrelated pending work untouched. It does not take over
an active processing lease. Disabled indexing rejects the recovery command before DB
access. Logs record the reset count; successful repeated recovery neither duplicates
chunks nor calls inference again. This change does not run recovery against any deployed DB.

## Activation and deployment ownership

`DISCLOSURE_ENABLED` is shared by portfolio-builder and disclosure-indexer and defaults
to false in both Compose configurations and service settings. An existing dev environment
with no disclosure model settings keeps the previous portfolio-generation path. The
indexer logs `disclosure_index_skipped` and exits before opening a DB connection or
calling inference. OpenDART collection continues independently.

When the flag is true, missing or inconsistent model settings remain startup errors.
There is no fallback to the news model, no fabricated disclosure evidence, and no bypass
of the enabled search requirement. Both new portfolio creation and enabled indexing
validate their model configuration before running. Re-explaining an existing portfolio
does not need a disclosure model.

Before enabling Modal inference, the deployment environment must contain:

| Key | Required value |
| --- | --- |
| DISCLOSURE_ENABLED | true |
| DISCLOSURE_INFERENCE_BACKEND | modal |
| DISCLOSURE_EMBEDDING_DIMENSIONS | 1024 |
| DISCLOSURE_EMBEDDING_MAX_TOKENS | 16384 |
| DISCLOSURE_MODAL_DOCUMENT_URL | Deployed authenticated document endpoint |
| DISCLOSURE_MODAL_QUERY_URL | Deployed authenticated query endpoint |
| DISCLOSURE_MODAL_RERANK_URL | Deployed authenticated reranker endpoint |
| DISCLOSURE_MODAL_KEY / DISCLOSURE_MODAL_SECRET | Workspace proxy credentials |

The existing CD SSM renderer materializes keys named in `DEV_AI_ENV`. The Cloud-owned
environment/SSM configuration must include the activation keys and credential references;
AI code owns validation and Compose forwarding. This PR changes neither Cloud resources
nor repository variables. Deploy and verify #430 endpoints before setting the flag true.
The false default allows #432 to be deployed before model serving is configured.

## Backend polling contract

Endpoint: `GET /api/v1/users/ai-server` with the existing authentication.

Add `investment_profile` to each user, beside `user_id` and `accounts`:

```json
{
  "users": [
    {
      "user_id": 7,
      "accounts": [],
      "investment_profile": {
        "risk_level": 2,
        "strategy": "Prefer dividends and exclude semiconductor stocks."
      }
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `investment_profile` | Optional; omitted or null preserves the existing response contract. |
| `risk_level` | Required strict integer, 1–5. Strings, booleans, and fractional values are rejected. |
| `strategy` | Optional free text, at most 4,000 characters; defaults to an empty string. |

Levels: 1 conservative, 2 moderately conservative, 3 balanced, 4 moderately aggressive,
5 aggressive. These correspond to 안정형, 안정추구형, 위험중립형, 적극투자형, 공격투자형.

This is the AI-side receiving contract. The backend must add the field to its response.
Profiles are parsed and retained, but do not yet affect portfolio generation, orders,
or position sizing. The text is not inserted into a model prompt or logged by this change.

## Verification

- Tests reproduce default deployment without disclosure model settings, reject incomplete
  enabled configuration, and verify a technical-only portfolio can save when disabled.
  Existing enabled-mode tests still require successful disclosure search.
- Final accuracy follow-up suite against disposable `ktb_disclosure_accuracy_test` PostgreSQL:
  **1,472 passed, 49 skipped, 2 subtests passed**. Migration 0016 was downgraded and
  upgraded in this test DB to verify the explicit empty state. Unconfigured optional integrations
  account for the skips.
- The branch includes dev 77fa8ef, including the merged collection fixes and stock-detail
  changes. Collection revisions 0013–0015 are unchanged; the only index revision is 0016.
- Python 3.14, existing CI job structure and hash-pinned Docker requirements match dev.
  CI traverses reverse dependencies for workspace services as well as packages, so a
  retrieval-only change also tests/builds portfolio-builder. Image jobs include only
  services with Dockerfiles; indexer requirements are checked against uv.lock.
  The actual CI shell script was exercised with retrieval-only, indexer-only, core-only
  and full-workspace inputs. All expected dependent test/build targets were present.
- Ruff, formatting, Tach internal and external dependencies, and diff checks pass.
- Retrieval tests call Python functions directly and retain PostgreSQL search,
  correction, source-identity, history, candidate-50, and reranking cases. HTTP endpoint
  tests were replaced with direct-call tests; server healthcheck tests were removed.
- Tool tests read real SQLite financial/section rows while HTTP is forbidden, preserve
  reranker scores, and exclude internal embedding input from tool output.
- A single-connection pool test proves embedding and reranking do not retain a DB connection.
- Local and production Compose configurations contain no retrieval service; model
  settings are passed to indexer and builder. Service secret files are not read for checks.
- Builder and shared-image packaging includes retrieval code and regenerated hash-pinned
  requirements. Retrieval code does not construct engines or declare a redundant DB driver.
- Builder and shared Docker images build; network-disabled imports find no retrieval
  API or client. The builder image also has no FastAPI installation.
- Oversized UTF-8 queries remain recoverable tool errors rather than aborting the agent.
- Collection PR #441 parses API responses before storing filing metadata, documents,
  sections and financial accounts in the same transaction. API failures save no partial
  record; database failures roll back every related row. `financials_collected` records
  completed financial data and is not used as a queue marker. No pending-row readback
  or separate processing pass remains.


The runtime and existing dependency versions follow the current dev Python 3.14 lockfile. No production database, service deployment, GPU deployment,
model inference, SQS publication, or backend repository modification was performed.
