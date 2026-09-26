# news-graph-builder — Cluster Summaries and Knowledge Graph

**Date:** 2026-09-24
**Status:** Draft for review
**Depends on:** #19, #27, #29 (news-clusterer). Open this PR after #29 merges, or stack it
on top of `feat/10/news-clusterer`.

## 1. Purpose and scope

A new cron service, `news-graph-builder`, that turns each news cluster into a title, a
summary and a small knowledge graph with one LLM call. Entities that are KOSPI companies
resolve to a shared company node, so the graph connects clusters through the companies
they mention. It takes over summarization from `news-clusterer`, which from now on only
clusters.

### Non-goals

- A fixed entity or relation type taxonomy. Types are free-form LLM strings in the MVP.
- Alias merging beyond the seeded company names (§5).
- Cleaning up entities that no cluster references any more.
- Companies outside KOSPI.
- Any graph query API or visualization.
- Deployment mechanics (ECS task, schedule).

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Trigger | Cron: `main()` runs once and exits | Same batch model as the other news services |
| Unit of extraction | One cluster (an event) | Clusters already group the articles about one event |
| LLM call | One call returns `title`, `summary`, `entities`, `relations` | Summary and graph come from the same reading of the articles |
| LLM input | Member article bodies, newest first, under a character budget | The summary needs the bodies anyway |
| Summarization owner | `news-graph-builder`; removed from `news-clusterer` | One service owns every LLM call on clusters |
| Storage | PostgreSQL tables in the `news` database | No new datastore; `portfolio-builder` reads it like everything else |
| Company list | Kiwoom REST `ka10099` (`mrkt_tp=0`) decides KOSPI membership | DART does not say which market a company trades on |
| Company details | DART `corp_codes` (via OpenDartReader) adds `corp_code` and `corp_eng_name`, joined on `stock_code` | Kiwoom has neither |
| Entity identity | Company alias lookup, else unique `(name, type)` where `name = normalize(raw_name)` | Deterministic; no threshold to tune |
| Summary table | `cluster_summaries`, owned by news-graph-builder; `title` / `summary` / `summarized_at` leave `clusters` | One writer per table; a missing row makes a cluster stale, so no backfill hack |
| Staleness marker | No `cluster_summaries` row, or `cluster_updated_at < clusters.updated_at` | The stored value is the `updated_at` that was summarized, not a wall-clock time |
| Concurrency with the clusterer | Optimistic check on `updated_at` inside the write transaction (§4.1) | The LLM call is long and the clusterer can change the cluster meanwhile |

### Table ownership

- `news-clusterer` writes `clusters` and `article_clusters`.
- `news-graph-builder` writes `cluster_summaries`, `companies`, `company_aliases`,
  `entities`, `cluster_entities` and `relations`. It only reads `clusters`.

## 3. Changes to news-clusterer (#29)

- Delete `summarize.py` and `tests/test_summarize.py`.
- `storage.py`: move `clusters_needing_summary`, `cluster_articles` and `set_summary` out
  (they become graph-builder's `cluster/repository.py`, with `set_summary` replaced by the
  guarded write in §4.1). Remove their cases from `tests/test_storage.py`; keep the write tests,
  asserting `updated_at` instead of the needs-summary query. The `clusters` table
  definition drops `title`, `summary` and `summarized_at`.
- `__main__.py`: remove the `httpx.Client`, the summary loop and the "exit 1 if a summary
  failed" rule. `tests/test_main.py`: remove the `summarize` fakes and the failed-summary
  test; the second-run test asserts that nothing is written.
- `settings.py`: remove `llm_base_uri`, `llm_model`, `summary_max_chars`, `llm_timeout`,
  and their cases in `tests/test_settings.py`.
- `pyproject.toml`: remove `httpx`; the description becomes "Cron-triggered news
  clustering". `uv lock`, then regenerate `docker/requirements/news-clusterer.txt`.
- `compose.dev.yaml`: remove `NEWS_CLUSTERER_LLM_BASE_URI` / `NEWS_CLUSTERER_LLM_MODEL`.
- `docs/superpowers/specs/2026-09-23-news-clusterer-design.md`: add a note at §6 that
  summaries moved to this spec.

## 4. Run flow

1. **Sync companies** (§6). On failure, log it and continue with the existing
   `companies` table. If the sync failed **and** `companies` is empty, exit 1 before
   extracting anything, so a first run cannot turn every company into a plain entity.
2. **Load stale clusters:** `c.id, c.updated_at FROM clusters c LEFT JOIN cluster_summaries s
   ON s.cluster_id = c.id WHERE s.cluster_id IS NULL OR s.cluster_updated_at <
   c.updated_at`, ordered by `c.id`.
3. **For each stale cluster:**
   1. Load member articles ordered by `published_at DESC` and build the prompt text:
      `title\n\nbody` blocks, stopping before the total exceeds `summary_max_chars`; the
      newest article is always included, truncated to the budget.
   2. `extract()` (§7): one LLM call, outside any transaction.
   3. Guarded write (§4.1).
4. On any HTTP, timeout or parse error for a cluster, log it, leave the cluster stale and
   continue. Exit 1 if the company sync or any cluster failed, else 0.

With no changed clusters the run syncs companies and makes no LLM call.

### 4.1 Guarded write

One transaction per cluster:

1. `SELECT 1 FROM clusters WHERE id = :id AND updated_at = :seen FOR SHARE`, where
   `:seen` is the `updated_at` read in step 2. No row means the clusterer changed or
   deleted the cluster during the LLM call: log at INFO, skip, not a failure. The next
   run picks it up again if it still exists.
2. Resolve entities (§5).
3. Delete the cluster's `cluster_entities` and `relations`, then insert the new ones.
   Re-extraction replaces, it never appends.
4. Upsert `cluster_summaries (cluster_id, title, summary, cluster_updated_at = :seen,
   summarized_at = now())`. Storing `:seen` keeps the cluster stale if `updated_at` moves
   after this commit.

The `FOR SHARE` lock makes the clusterer's update or delete of that row wait for this
short transaction; its cascade delete then removes the rows just written.

## 5. Schema (migration `0003`) and entity resolution

```
companies
  corp_code      text PRIMARY KEY
  stock_code     text NOT NULL
  corp_name      text NOT NULL
  corp_eng_name  text NULL
  synced_at      timestamptz NOT NULL DEFAULT now()

company_aliases
  alias      text PRIMARY KEY          -- normalize()d
  corp_code  text NOT NULL REFERENCES companies(corp_code)

entities
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
  raw_name   text NOT NULL        -- first spelling seen
  name       text NOT NULL        -- normalize(raw_name)
  type       text NOT NULL
  corp_code  text NULL REFERENCES companies(corp_code)
  UNIQUE (corp_code) WHERE corp_code IS NOT NULL
  UNIQUE (name, type) WHERE corp_code IS NULL

cluster_summaries
  cluster_id          bigint PRIMARY KEY REFERENCES clusters(id) ON DELETE CASCADE
  title               text NOT NULL
  summary             text NOT NULL
  cluster_updated_at  timestamptz NOT NULL   -- clusters.updated_at that was summarized
  summarized_at       timestamptz NOT NULL DEFAULT now()

cluster_entities
  cluster_id  bigint REFERENCES clusters(id) ON DELETE CASCADE
  entity_id   bigint REFERENCES entities(id)
  PRIMARY KEY (cluster_id, entity_id)
  INDEX (entity_id)

relations
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
  cluster_id        bigint NOT NULL REFERENCES clusters(id) ON DELETE CASCADE
  source_entity_id  bigint NOT NULL REFERENCES entities(id)
  target_entity_id  bigint NOT NULL REFERENCES entities(id)
  type              text NOT NULL
  description       text NOT NULL
  INDEX (cluster_id), INDEX (source_entity_id), INDEX (target_entity_id)
```

Migration `0003` also drops `title`, `summary` and `summarized_at` from `clusters`.
Every existing cluster then has no `cluster_summaries` row and is stale, so the first run
makes one LLM call per existing cluster and rebuilds the summaries #29 wrote.

For a company entity, `raw_name` is `corp_name`, `name` is `normalize(corp_name)` and
`type` is `기업`.

- Every foreign key to `clusters` cascades, so the clusterer's delete of an unmatched
  cluster never fails on graph rows.
- `stock_code` is deliberately not unique: companies are never deleted, so a code that
  later moves to another `corp_code` would otherwise fail every sync. The join happens
  in Python and nothing reads by `stock_code`.
- The sync upserts companies and never deletes them. A company that leaves KOSPI keeps
  its row and its entity; `synced_at` shows when it was last on the list.
- Entities are shared across clusters. The only deletion is a plain entity merged into
  a company (§6 step 5).

### Resolution (`graph/service.py`)

`normalize(text)`: remove `(주)`, `㈜` and `주식회사`, remove all whitespace, casefold. The
same function builds aliases and looks them up.

For each entity the LLM returns:

1. **Alias hit** on `normalize()` of the LLM's name: use the company's entity (upsert
   on `corp_code`), ignoring the LLM's type, so "기업", "회사" and "company" land on one node.
2. **Alias miss:** upsert on `(name, type)`, with the LLM's name as `raw_name` and
   `name = normalize(raw_name)`. The `ON CONFLICT` clause carries the partial index
   predicate (`index_where=corp_code IS NULL`).

Then:

- Deduplicate the resolved entity ids before inserting `cluster_entities`.
- Match each relation's `source` and `target` to the returned entities by
  `normalize()`d name. Drop relations with an unknown endpoint and log how many.
- A cluster with zero entities still gets its title and summary.

## 6. Company sync (`company/service.py`)

The fetches stay at the edge; the join and upsert take plain rows so tests need no
patched clients.

1. **Kiwoom:** `POST {kiwoom_base_uri}/oauth2/token` with
   `{"grant_type": "client_credentials", "appkey", "secretkey"}`, then
   `POST {kiwoom_base_uri}/api/dostk/stkinfo` with headers `api-id: ka10099` and
   `authorization: Bearer <token>`, body `{"mrkt_tp": "0"}`, following continuation
   headers until the list ends. Keep `code` and `name`.
2. **DART:** `opendartreader.dart_list.corp_codes(dart_api_key)`, called directly: the
   `OpenDartReader` constructor also loads `.env` from the working directory and writes a
   pickle cache to `./docs_cache/`, neither of which a cron task wants. Keep listed rows
   (non-blank `stock_code`) with `corp_code`, `corp_name`, `corp_eng_name`, `stock_code`.
   `requests` puts the full URL, which carries the key, into connection error messages,
   so a failure is re-raised as a new error without the original chained.
3. **Join** on `stock_code`. Log the Kiwoom, DART and joined counts. A joined count below
   the Kiwoom count is expected (preferred shares have no DART row); **zero joined rows
   is a failure** (it means the code formats do not match).
4. **Upsert** `companies` (updating names and `synced_at`), then insert aliases from
   `corp_name`, the Kiwoom `name` and `corp_eng_name` with `ON CONFLICT DO NOTHING`.
   A name two companies share keeps its first owner; the other falls through to a
   plain entity. Aliases are never deleted, so they can also be added by hand.
5. **Merge plain entities into companies**, in the same transaction as step 4. A plain
   entity (`corp_code IS NULL`) whose `name` equals an alias, of any type, is
   merged into that alias's company entity:
   1. Upsert the company entity for the alias's `corp_code`.
   2. Repoint `relations.source_entity_id` and `relations.target_entity_id` to it.
   3. `INSERT INTO cluster_entities … SELECT … ON CONFLICT DO NOTHING` for the company
      entity, then delete the plain entity's `cluster_entities` rows. This covers a
      cluster that already had both.
   4. Delete the plain entity.

   This runs on every sync, so it catches both newly listed companies (their name was a
   plain entity in earlier news) and aliases added by hand to fix a miss. With nothing to
   merge it is a no-op, and it needs no LLM call. Log the number of merged entities.

### Credentials

- The Kiwoom app key can place trades. Prefer a paper-trading (모의투자) key or a
  dedicated account; `kiwoom_base_uri` switches the domain.
- Kiwoom only accepts requests from registered IP addresses, so the production task's
  outbound IP has to be registered.
- All keys come from the environment and are never committed.

## 7. LLM extraction (`graph/llm.py`)

`POST {llm_base_uri}/chat/completions`, the same shape as the clusterer's former
`summarize()`: `model`, a Korean system prompt, the article text as the user message,
and `response_format` of type `json_schema`:

```json
{
  "title": "string",
  "summary": "string",
  "entities": [{"name": "string", "type": "string"}],
  "relations": [
    {"source": "string", "target": "string", "type": "string", "description": "string"}
  ]
}
```

- The prompt asks for a one-line event title, a 3–5 sentence summary, and at most
  `max_entities` entities and `max_relations` relations, all in Korean, with relation
  endpoints named exactly as in `entities`. The cap keeps replies from being truncated,
  which would otherwise fail the same cluster on every run.
- The reply is validated at the boundary: every field present with the right type,
  otherwise `ValueError`. Entries beyond the caps are cut.

## 8. Code, configuration and packaging

### Modules (`services/news-graph-builder/src/news_graph_builder/`)

Organized by domain, NestJS-style: each domain package exposes a public API in its
`__init__.py`; `service.py` holds decisions, `repository.py` holds SQL, `dto.py` holds data
shapes. `tach.toml` enforces the dependencies and interfaces between the packages.

| Module | Contents |
|---|---|
| `__main__.py` | `main()` — §4 |
| `settings.py` | `Settings` |
| `database.py` | `metadata` and the table mirrors (the migrations own the schema) |
| `common/normalize.py` | `normalize()` — §5 |
| `company/kiwoom.py` | `fetch_kospi()` — §6 step 1 |
| `company/dart.py` | `fetch_corp_codes()` — §6 step 2 |
| `company/dto.py` | `DartCompany` |
| `company/service.py` | `sync_companies()` — §6 steps 3–5, on plain rows |
| `company/repository.py` | company upserts, aliases, entity merge, `find_corp_code()`, `upsert_company_entity()` |
| `cluster/repository.py` | `find_stale_clusters()`, `find_cluster_articles()`, `lock_cluster()` — §4.1 step 1 |
| `graph/dto.py` | `Entity`, `Relation`, `Extraction` |
| `graph/llm.py` | `extract()` — §7 |
| `graph/service.py` | `resolve()` — §5 |
| `graph/repository.py` | plain-entity upsert, `write_graph()` — §4.1 steps 3–4 |

Dependencies: `graph` → `company`; `company`, `graph` → `database`, `common`;
`cluster` → `database`.
`company` never imports `graph`.

### Settings (`NEWS_GRAPH_BUILDER_` prefix)

| Setting | Default |
|---|---|
| `postgres_dsn` | required |
| `llm_base_uri` | required (includes `/v1`) |
| `llm_model` | required |
| `kiwoom_app_key` | required |
| `kiwoom_secret_key` | required |
| `dart_api_key` | required |
| `kiwoom_base_uri` | `https://api.kiwoom.com` |
| `summary_max_chars` | `24000` |
| `llm_timeout` | `120` seconds |
| `max_entities` | `30` |
| `max_relations` | `50` |
| `log_level` | `INFO` |

### Dependencies and packaging

- `services/news-graph-builder/pyproject.toml`: console script `news-graph-builder`;
  depends on `ktb-core`, `httpx`, `sqlalchemy`, `psycopg[binary]`, `pydantic-settings`,
  `opendartreader`, at the versions the other services use.
- `tach.toml`: add the source root and a `news_graph_builder` module depending on
  `ktb_core`.
- `docker/news-graph-builder.Dockerfile`, modeled on the clusterer's. Calling
  `dart_list.corp_codes` directly writes no cache, so the read-only `WORKDIR` is fine.
- `uv lock`, then `uv export` to `docker/requirements/news-graph-builder.txt` as in
  `AGENTS.md`.
- `compose.dev.yaml`: a `news-graph-builder` service under the `jobs` profile, depending
  on a healthy `postgres`, `restart: "no"`, passing the `NEWS_GRAPH_BUILDER_*` variables
  through.
- CI: add `news-graph-builder` to the service list in `ci-dev.yaml` (the requirements
  check loop) and to the image build matrices in `ci-dev.yaml` and `ci-main.yaml`.

### Documentation

- `AGENTS.md`: a member-table row (cron, depends on `ktb-core`), the datastore-flow bullet
  (clusterer writes `clusters`, graph-builder writes `cluster_summaries` and the graph;
  readers join `clusters` to `cluster_summaries` for titles), the
  settings-prefix list and the compose run command.

## 9. Verification status

Checked during planning (2026-09-25):

- DART `corp_codes` returns `corp_code`, `corp_name`, `corp_eng_name`, `stock_code`,
  `modify_date`: 119,447 rows, 3,994 with a `stock_code`, always six digits; 3 listed
  rows have no English name.
- `opendartreader` 0.3.3 installs and imports on Python 3.13 and 3.14.
- Kiwoom paging uses `cont-yn` / `next-key` request and response headers (library docs).

Still open, covered by the zero-join guard and the first real run:

- Kiwoom `code` uses the same six-digit format as DART `stock_code`.
- The paper-trading domain serves `ka10099`.
- vLLM accepts the nested `json_schema` in §7.

## 10. Limits

- LLM cost scales with changed clusters. After `0003` the first run is a one-time pass
  over every existing cluster.
- Entity types are free-form, so the same non-company entity can appear under several
  types. Company resolution ignores type; everything else waits for a later taxonomy.
- Company names outside the seeded aliases (nicknames, group names such as "SK") stay
  plain entities until an alias is added; the next sync then merges them (§6 step 5).
- A merge is only as correct as the alias: a person or product sharing a company's
  normalized name is merged into that company. Forward resolution takes the same risk.

## 11. Testing

Follows the existing patterns: `httpx.MockTransport` for HTTP, PostgreSQL fixtures skipped
without `KTB_TEST_POSTGRES_DSN`. The fixtures truncate tables, so locally
`KTB_TEST_POSTGRES_DSN` points at a separate `news_test` database, never at `news`.

- `normalize`: `(주)`, `㈜`, `주식회사`, inner whitespace, English casefolding.
- `sync_companies`: Kiwoom token and paging over `MockTransport`; the join/upsert on plain
  rows: a KOSPI company without a DART row is skipped, an alias shared by two companies
  keeps its first owner, zero joined rows raises, a second sync updates names and
  `synced_at` without duplicating rows.
- merge (PostgreSQL): a plain entity with relations on both ends and cluster membership
  is merged into a newly aliased company; a cluster that held both the plain entity and
  the company keeps one `cluster_entities` row; plain entities of two types with the
  same name both merge; a second sync merges nothing.
- `extract`: a valid reply, malformed JSON, a missing field, an HTTP error, and entries
  beyond the caps being cut.
- `resolve`: alias hit regardless of type; miss creates one entity per
  `(name, type)`, keeping the first `raw_name`; two names resolving to one company are
  deduplicated; a relation with an unknown endpoint is dropped.
- `cluster` / `graph` repositories: re-extraction replaces relations; the guarded write skips
  when `updated_at`
  moved and when the cluster was deleted; `cluster_updated_at` is set to the seen
  `updated_at`; a cluster with no summary row and one with an older
  `cluster_updated_at` are both stale; deleting a cluster cascades to its graph rows.
- `main`: end to end with mocked LLM, Kiwoom and DART; a second run makes no LLM call;
  a failed extraction exits 1 and is retried next run; a failed sync with an empty
  `companies` table exits 1 before any LLM call.
- `test_migrations`: `0003` upgrades and downgrades, creates `cluster_summaries` and
  drops `title`, `summary` and `summarized_at` from `clusters`.
- news-clusterer: its trimmed tests pass without any LLM setting.
