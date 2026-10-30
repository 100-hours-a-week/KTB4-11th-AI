# Disclosure services review follow-up

Date: 2026-10-30

## Scope and decisions

This follow-up applies the review of PR #432 and separates collection issue #440
from indexing/retrieval issue #346 and GPU serving issue #431. Collection has its own
PR, #432 follows that branch, and #430 follows #432. Each PR closes its own issue.
The root checkout and running services are not changed.

The existing HTTP retrieval service remains in place. Portfolio-builder calls it
through `RetrievalClient`. FastAPI receives those requests; it is not needed for
outgoing Modal calls. A direct Python-call design would remove that HTTP boundary
and move database and inference dependencies into its caller. That architectural
change is outside this follow-up.

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
   indexer and retrieval containers through Compose environment mappings.
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
   reads and encode query parameters before sending them upstream.
4. Remove `portfolio_builder/disclosures.py`. Keep its search-response validation
   beside the HTTP client in `retrieval_client.py`; reuse it in the submission hook.
   The existing successful-narrative-search requirement before submission remains.
5. Receive an optional investment profile in the existing backend polling response.
   Do not add an endpoint, polling loop, model call, or trading rule for this field.

The proposed direct-DB tools and whole-service performance benchmark are subsequent
work. This PR split does not replace the current HTTP retrieval boundary or claim a
new performance result.

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
- Final full suite against the disposable `ktb_test` PostgreSQL database:
  **1,341 passed, 49 skipped, 2 subtests passed**. Unconfigured optional integrations
  account for the skips.
- Ruff, formatting, Tach internal and external dependencies, and diff checks pass.
- Local and production Compose configurations parse; all 10 model repository/revision/hash
  overrides reach both disclosure containers. Service secret files were not read for this check.
- News-preprocessor and portfolio-builder Docker images build. Network-disabled containers
  import the relocated collection entrypoint and all three disclosure tools.
- Read-only code review found no critical or important issues. Direct-read response checks
  currently validate the envelope and required fields; search responses retain typed validation.

No dependency versions changed. No production database, service deployment, GPU deployment,
model inference, SQS publication, or backend repository modification was performed.
