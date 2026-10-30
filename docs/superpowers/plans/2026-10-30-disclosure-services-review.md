# Disclosure services review follow-up

Date: 2026-10-30

## Scope and decisions

This follow-up applies the review of PR #432 and separates collection issue #440 / PR #441
from indexing/retrieval issue #346 and GPU serving issue #431. Collection has its own
PR, #432 follows that branch, and #430 follows #432. Each PR closes its own issue.
The root checkout and running services are not changed.

Portfolio-builder calls the `packages/disclosure-retrieval` Python library directly.
Its tools receive the builder's SQLAlchemy engine and component model settings.
There is no separate retrieval HTTP server, controller, router, client, port,
healthcheck, console command, or Docker container. The library declares no FastAPI
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
2. Move periodic-report collection to
   `news_preprocessor/sources/publishers/opendart/disclosures/`. Keep the
   `news-preprocessor-disclosures` command. Short filings still use the news publisher;
   periodic reports store originals, documents, sections, and financial facts without
   embedding during collection. This code, its archive/Compose configuration, daily
   collector timer, and migrations 0012–0014 belong to the collection PR. The indexing
   PR contributes only migration 0015 for vectors. After collection completes or
   finally fails, systemd starts the indexer; a second timer does not repeat collection.
3. Put portfolio-builder's disclosure tools in `tools/disclosure/search.py`,
   `get.py`, and `tool.py`. Expose `search_disclosures`, `get_disclosure_sections`,
   and `get_disclosure_financials`. Require an explicit year or period for financial
   reads. Execute parameterized SQL in-process using the existing engine. Search
   orchestrates DB reads and remote model calls through the library. Query and
   reranking inference run after the DB connection has returned to its pool.
4. Remove `portfolio_builder/disclosures.py` and `retrieval_client.py`. Reuse the
   library's typed `SearchResult` in the submission hook through Pydantic TypeAdapter;
   its three result lists are required, so malformed `{}` cannot count as a search.
   The existing successful-narrative-search requirement before submission remains.
5. Receive an optional investment profile in the existing backend polling response.
   Do not add an endpoint, polling loop, model call, or trading rule for this field.

Only external model inference uses HTTPS. Financial and section reads make no HTTP
or GPU call. The whole-service performance benchmark remains subsequent work;
removing HTTP is not itself a measured latency or throughput improvement.

Model overrides must match the GPU serving checkpoint and identity. Changing a CPU
environment variable does not deploy different GPU weights. Incompatible serving
identities are rejected. Changing embedding metadata creates a different index space;
old vectors are not silently reused.

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

- Added tests failed first for missing profile parsing, direct-read methods, and tools.
- Final full suite against the disposable `ktb_disclosure_split_test` PostgreSQL database:
  **1,347 passed, 49 skipped, 2 subtests passed**. Unconfigured optional integrations
  account for the skips.
- Ruff, formatting, Tach internal and external dependencies, and diff checks pass.
- Retrieval's 48 tests call Python functions directly and retain PostgreSQL search,
  correction, source-identity, history, candidate-50, and reranking cases. HTTP endpoint
  tests were replaced with direct-call tests; server healthcheck tests were removed.
- Tool tests read real SQLite financial/section rows while HTTP is forbidden, preserve
  reranker scores, and exclude internal embedding input from tool output.
- A single-connection pool test proves embedding and reranking do not retain a DB connection.
- Local and production Compose configurations contain no retrieval service; model
  settings are passed to indexer and builder. Service secret files are not read for checks.
- Builder and shared-image packaging includes the library and regenerated hash-pinned
  requirements. The library does not construct engines or declare a redundant DB driver.
- Builder and shared Docker images build; network-disabled imports find no retrieval
  API or client. The builder image also has no FastAPI installation.
- Oversized UTF-8 queries remain recoverable tool errors rather than aborting the agent.
- The PR split exposed a financial-collection retry problem. Collection PR #441 fixes it
  with an independent completion Boolean in migration 0014. Comparing the original and
  split PostgreSQL schemas confirms that original columns, constraints, and indexes remain;
  only this completion state and its NOT NULL constraint were added.

No dependency versions changed. No production database, service deployment, GPU deployment,
model inference, SQS publication, or backend repository modification was performed.
