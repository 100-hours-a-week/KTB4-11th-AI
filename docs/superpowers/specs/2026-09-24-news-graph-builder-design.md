# news-graph-builder — Cluster Summaries and Knowledge Graph

**Date:** 2026-09-24
**Status:** Draft for review (revised 2026-09-26: themes, §7)
**Depends on:** #19, #27, #29 (news-clusterer). Open this PR after #29 merges, or stack it
on top of `feat/10/news-clusterer`.

## 1. Purpose and scope

A new cron service, `news-graph-builder`, that turns each news cluster into a title, a
summary and a small knowledge graph with one LLM call. Entities that are KOSPI companies
resolve to a shared company node, so the graph connects clusters through the companies
they mention. It takes over summarization from `news-clusterer`, which from now on only
clusters. It also keeps a reference copy of Kiwoom's themes and their KOSPI 200 members
(§7) for `portfolio-builder`; themes are not part of the graph.

### Non-goals

- A fixed entity or relation type taxonomy. Types are free-form LLM strings in the MVP.
- Alias merging beyond the seeded company names (§5).
- Cleaning up entities that no cluster references any more.
- Companies outside KOSPI.
- Theme nodes in the graph, or resolving theme names the LLM extracts from news. Themes are
  reference tables only; `entities` and `relations` come only from news clusters.
- Theme members outside KOSPI 200.
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
| Themes | Kiwoom `ka90001` (all themes) and `ka90002` (one theme's stocks), stored in `themes` / `theme_companies` | Kiwoom is the only theme source; portfolio-builder reads the tables directly |
| Theme membership | Only stocks that are KOSPI 200 constituents (Kiwoom `ka20002`, sector `201`) and already in `companies` | The graph and portfolio-builder work on large caps; the `corp_code` FK keeps membership joinable to company nodes |
| Theme refresh | Every run, full replace of both tables in one transaction | Nothing references the rows, so there is no identity to preserve; readers always see one consistent snapshot |
| Concurrency with the clusterer | Optimistic check on `updated_at` inside the write transaction (§4.1) | The LLM call is long and the clusterer can change the cluster meanwhile |

### Table ownership

- `news-clusterer` writes `clusters` and `article_clusters`.
- `news-graph-builder` writes `cluster_summaries`, `companies`, `company_aliases`,
  `entities`, `cluster_entities`, `relations`, `themes` and `theme_companies`. It only
  reads `clusters`.

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
2. **Sync themes** (§7), against whatever `companies` holds now. On failure, log it, keep
   the existing theme tables and continue: the graph does not depend on themes, and a
   theme failure does not change the exit code.
3. **Load stale clusters:** `c.id, c.updated_at FROM clusters c LEFT JOIN cluster_summaries s
   ON s.cluster_id = c.id WHERE s.cluster_id IS NULL OR s.cluster_updated_at <
   c.updated_at`, ordered by `c.id`.
4. **For each stale cluster:**
   1. Load member articles ordered by `published_at DESC` and build the prompt text:
      `title\n\nbody` blocks, stopping before the total exceeds `summary_max_chars`; the
      newest article is always included, truncated to the budget.
   2. `extract()` (§8): one LLM call, outside any transaction.
   3. Guarded write (§4.1).
5. On any HTTP, timeout or parse error for a cluster, log it, leave the cluster stale and
   continue. Exit 1 if the token request, the company sync or any cluster failed, else 0.

One Kiwoom access token is issued per run and shared by steps 1 and 2; if issuing it fails,
both syncs count as failed. With no changed clusters the run syncs companies and themes and
makes no LLM call.

### 4.1 Guarded write

One transaction per cluster:

1. `SELECT 1 FROM clusters WHERE id = :id AND updated_at = :seen FOR SHARE`, where
   `:seen` is the `updated_at` read in step 3. No row means the clusterer changed or
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

1. **Kiwoom:** with the run's token (issued by `POST {kiwoom_base_uri}/oauth2/token`,
   `{"grant_type": "client_credentials", "appkey", "secretkey"}`),
   `POST {kiwoom_base_uri}/api/dostk/stkinfo` with headers `api-id: ka10099` and
   `authorization: Bearer <token>`, body `{"mrkt_tp": "0"}`, following continuation
   headers until the list ends. Keep `code` and `name`. Token issuing and paging live in
   the shared `kiwoom` module, which the theme sync (§7) uses too.
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
  dedicated account; `kiwoom_base_uri` switches the domain. The theme and sector
  endpoints are read-only market data like `ka10099`.
- Kiwoom only accepts requests from registered IP addresses, so the production task's
  outbound IP has to be registered.
- All keys come from the environment and are never committed.

## 7. Theme sync (`theme/service.py`)

Themes are Kiwoom's stock groupings (e.g. `2차전지`, `HBM`). They are reference data for
`portfolio-builder`: no theme node, no relation, no LLM resolution.

### Schema (migration `0004`)

```
themes
  theme_code  text PRIMARY KEY          -- Kiwoom thema_grp_cd
  name        text NOT NULL             -- Kiwoom thema_nm
  synced_at   timestamptz NOT NULL DEFAULT now()

theme_companies
  theme_code  text NOT NULL REFERENCES themes(theme_code) ON DELETE CASCADE
  corp_code   text NOT NULL REFERENCES companies(corp_code) ON DELETE CASCADE
  is_main     boolean NOT NULL DEFAULT false   -- listed in the theme's main_stk
  PRIMARY KEY (theme_code, corp_code)
  INDEX (corp_code)
```

A reader goes from a company node to its themes with `entities.corp_code →
theme_companies → themes`, and from a theme to news with the reverse join through
`cluster_entities`.

### Steps

All three endpoints are `POST {kiwoom_base_uri}/api/dostk/<path>` with the run's token and
the same `cont-yn` / `next-key` paging as `ka10099`; the shared `kiwoom` module waits
`kiwoom_request_interval` seconds between calls.

1. **Themes:** `ka90001` (테마그룹별요청) on `thme` with
   `{"qry_tp": "0", "date_tp": "1", "flu_pl_amt_tp": "1", "stex_tp": "1"}`. From each
   `thema_grp` row keep `thema_grp_cd`, `thema_nm` and `main_stk` (주요종목). `qry_tp=0`
   means all themes; the other three fields are required by the API and only affect price
   statistics we ignore.
2. **KOSPI 200:** `ka20002` (업종별주가요청) on `sect` with
   `{"mrkt_tp": "2", "inds_cd": "201", "stex_tp": "1"}`. Keep `stk_cd` from each
   `inds_stkpc` row.
3. **Members:** for each theme, `ka90002` (테마구성종목요청) on `thme` with
   `{"thema_grp_cd": <code>, "stex_tp": "1", "date_tp": "1"}`. Keep `stk_cd` and `stk_nm`
   from each `thema_comp_stk` row.
4. **Codes:** Kiwoom may append a market suffix (`005930_AL`); requests use KRX only
   (`stex_tp=1`) and every `stk_cd` is cut at the first `_`.
5. **Filter:** keep a member only if its code is in the KOSPI 200 set **and** a
   `companies` row has that `stock_code`. If several rows share the code (§5), take the
   most recently synced one. Count the rest as skipped.
6. **Main flag:** a kept member gets `is_main = true` when it appears in its theme's
   `main_stk`. The format of `main_stk` is not documented, so it is split on `,`, each
   part is trimmed, and a member matches when a part equals its `stk_cd` or its
   `normalize()`d `stk_nm` equals the `normalize()`d part. A main stock that is not a kept
   member simply has no row.
7. **Guard:** zero themes, zero KOSPI 200 codes, or zero kept memberships is a failure,
   and nothing is written, so an outage never empties the tables.
8. **Replace**, in one transaction: delete `theme_companies` and `themes`, then insert
   every theme from step 1 (including themes with no KOSPI 200 member) and the kept
   memberships with their `is_main` flag.
9. **Log** `synced themes: themes=… kospi200=… members=… main=… skipped=…`.

The fetches stay at the edge; the filter and replace take plain rows, as in §6.

## 8. LLM extraction (`graph/llm.py`)

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

- The prompt follows the structure and rules of langchain-neo4j's `LLMGraphTransformer`, in
  Korean: sections for overview, entities, relations, coreference and strict compliance; use
  only facts stated in the articles; free-form but basic, general entity types (`인물`, not
  `반도체 전문가`); general, lasting relation types (`공급`, not `공급 계약을 체결함`); the most
  complete name for an entity mentioned several ways, and people without titles.
- The system prompt caps the reply at `max_entities` entities and `max_relations` relations.
  The cap keeps replies from being truncated, which would otherwise fail the same cluster on
  every run.
- The user message carries one worked example (a short article and its JSON reply) before
  the cluster's articles; a test parses the example reply with the same schema.
- The reply is validated at the boundary: every field present with the right type,
  otherwise `ValueError`. Entries beyond the caps are cut.

## 9. Code, configuration and packaging

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
| `kiwoom/client.py` | `fetch_token()`, `fetch_pages()` (paging, request interval) |
| `company/kiwoom.py` | `fetch_kospi()` — §6 step 1 |
| `company/dart.py` | `fetch_corp_codes()` — §6 step 2 |
| `company/dto.py` | `DartCompany` |
| `company/service.py` | `sync_companies()` — §6 steps 3–5, on plain rows |
| `company/repository.py` | company upserts, aliases, entity merge, `find_corp_code()`, `upsert_company_entity()` |
| `theme/dto.py` | `Theme`, `ThemeMember` |
| `theme/kiwoom.py` | `fetch_themes()`, `fetch_kospi200_codes()`, `fetch_theme_members()` — §7 steps 1–4 |
| `theme/service.py` | `sync_themes()` — §7 steps 5–9, on plain rows |
| `theme/repository.py` | `find_corp_codes_by_stock_code()`, `replace_themes()` |
| `cluster/repository.py` | `find_stale_clusters()`, `find_cluster_articles()`, `lock_cluster()` — §4.1 step 1 |
| `graph/dto.py` | `Entity`, `Relation`, `Extraction` |
| `graph/llm.py` | `extract()` — §8 |
| `graph/service.py` | `resolve()` — §5 |
| `graph/repository.py` | plain-entity upsert, `write_graph()` — §4.1 steps 3–4 |

Dependencies: `graph` → `company`; `company`, `graph` → `database`, `common`;
`company`, `theme` → `kiwoom`; `theme`, `cluster` → `database`; `theme` → `common`.
`company` never imports `graph`; `theme` imports neither `company` nor `graph`.

### Settings (`NEWS_GRAPH_BUILDER_` prefix)

Each domain owns its settings, like NestJS per-feature config. A function that needs them
takes `settings: X | None = None` and reads the environment when it gets `None`, so `main()`
only passes what it uses itself. All classes set `hide_input_in_errors=True`.

| Class | Setting | Default |
|---|---|---|
| `settings.Settings` | `postgres_dsn` | required |
| | `log_level` | `INFO` |
| `kiwoom.settings.KiwoomSettings` | `kiwoom_app_key` | required |
| | `kiwoom_secret_key` | required |
| | `kiwoom_base_uri` | `https://api.kiwoom.com` |
| | `kiwoom_request_interval` | `0.2` seconds (Kiwoom's own examples) |
| `company.settings.CompanySettings` | `dart_api_key` | required |
| `graph.settings.LlmSettings` | `llm_base_uri` | required (includes `/v1`) |
| | `llm_model` | required |
| | `llm_api_key` | none; sent as `Authorization: Bearer` when set |
| | `summary_max_chars` | `24000` |
| | `llm_timeout` | `120` seconds |
| | `max_entities` | `30` |
| | `max_relations` | `50` |

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

## 10. Verification status

Checked during planning (2026-09-25):

- DART `corp_codes` returns `corp_code`, `corp_name`, `corp_eng_name`, `stock_code`,
  `modify_date`: 119,447 rows, 3,994 with a `stock_code`, always six digits; 3 listed
  rows have no English name.
- `opendartreader` 0.3.3 installs and imports on Python 3.13 and 3.14.
- Kiwoom paging uses `cont-yn` / `next-key` request and response headers (library docs).

Checked 2026-09-26 from Kiwoom's official examples (`Kiwoom-Securities/Kiwoom-REST-API`),
not yet against the live API: `ka90001` / `ka90002` on `/api/dostk/thme` with the fields in
§7, `ka20002` on `/api/dostk/sect` with `mrkt_tp=2` / `inds_cd=201` for KOSPI 200.

Still open, covered by the zero-join guard and the first real run:

- Kiwoom `code` uses the same six-digit format as DART `stock_code`.
- The paper-trading domain serves `ka10099`.
- vLLM accepts the nested `json_schema` in §8.
- `stk_cd` from `ka90002` / `ka20002` matches `companies.stock_code` after the `_` cut.
- The format of `ka90001` `main_stk` (names or codes, one or several); §7 step 6 accepts
  either, and the log's `main=` count shows whether anything matched.
- Kiwoom's rate limit with one `ka90002` call per theme at `kiwoom_request_interval`.

## 11. Limits

- LLM cost scales with changed clusters. After `0003` the first run is a one-time pass
  over every existing cluster.
- Entity types are free-form, so the same non-company entity can appear under several
  types. Company resolution ignores type; everything else waits for a later taxonomy.
- Company names outside the seeded aliases (nicknames, group names such as "SK") stay
  plain entities until an alias is added; the next sync then merges them (§6 step 5).
- The theme sync makes one `ka90002` call per theme (a few hundred), so it adds about a
  minute per run at the default request interval.
- KOSPI 200 is rebalanced twice a year; memberships follow on the next run. A theme
  member that is KOSPI 200 but missing from `companies` (no DART row) is skipped.
- A merge is only as correct as the alias: a person or product sharing a company's
  normalized name is merged into that company. Forward resolution takes the same risk.

## 12. Testing

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
- `kiwoom`: token, paging with the request interval, a non-zero `return_code` raising.
- `theme`: the three fetches over `MockTransport` with the `_` cut; the filter keeps
  KOSPI 200 members found in `companies` and counts the rest; `is_main` is set from
  `main_stk` given as a code, a name, or a comma-separated list, and stays false otherwise;
  zero themes or zero KOSPI 200 codes raises and writes nothing; a replace removes a theme
  that disappeared; deleting a theme or a company cascades to its memberships.
- `main`: a failed theme sync is only logged: it keeps the old theme tables, still builds
  graphs and exits 0;
  one token serves both syncs.
- `test_migrations`: `0004` upgrades and downgrades and matches `database.py`; `0003`
  upgrades and downgrades, creates `cluster_summaries` and
  drops `title`, `summary` and `summarized_at` from `clusters`.
- news-clusterer: its trimmed tests pass without any LLM setting.
