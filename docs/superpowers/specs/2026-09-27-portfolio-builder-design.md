# portfolio-builder — Model Portfolio Agent

**Date:** 2026-09-27
**Status:** Draft for review
**Issue:** #36
**Depends on:** #30 (news-graph-builder: `cluster_summaries`, graph, themes). Technicals come
from `market-analyzer-mcp` (#37), which in turn depends on #32 (QuestDB `bars_*`).

## 1. Purpose and scope

`portfolio-builder` is rewritten as a TypeScript service on Bun that runs once per cron
trigger (`docker compose -f compose.dev.yaml up portfolio-builder`), builds **one model
portfolio** with a Pi agent, stores it as a new version, and exits. Every user receives the
same model portfolio; turning it into a per-user portfolio by rules happens elsewhere.

The agent starts from the previous portfolio, a briefing of recent news clusters, the KOSPI
companies they mention and those companies' themes, and may call tools for more news, graph
relations and technical analysis. It must justify every stock that enters or leaves.

### Non-goals

- `market-analyzer-mcp` itself (#37). This spec fixes only the contract it serves (§9).
- Per-user portfolios, the rule engine, the work queue trigger.
- Hard portfolio constraints: no maximum holdings, no maximum weight per stock.
- Deployment mechanics (ECS task, schedule).

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Trigger | Cron: `main()` runs once and exits | MVP; the queue consumer comes later |
| Output | One model portfolio per run, inserted as a new version | Every user shares it; history is kept |
| Previous portfolio | `ORDER BY created_at DESC, id DESC LIMIT 1` | Latest created wins; `id` breaks ties |
| Language / runtime | TypeScript on Bun | Pi is TypeScript; Bun runs TS directly, has `bun test` and `Bun.sql` built in |
| Agent | `@earendil-works/pi-agent-core` `Agent` with custom `AgentTool`s | Pi chosen over LangGraph |
| LLM | pi-ai `openai-codex` provider (ChatGPT subscription OAuth: access + refresh token) | Team decision; risk accepted (§8) |
| Context | Code-assembled briefing + a few tools | Bounded, testable context; tools for drill-down only |
| Grounding | Every reason must originate in the briefing or tool results; reasoning may draw on pre-trained knowledge | Stored reasons stay traceable to data without forbidding general understanding |
| Company key | `company_id` = `companies.corp_code`, enforced by FK | Existence is verified when writing, not by a separate read |
| Graph queries | Multi-hop traversal and path finding over `entities` / `relations` with recursive CTEs | Treat the graph as a graph without adding a graph database |
| News search | Postgres full-text search, `simple` config, prefix terms | No new extension; Postgres has no Korean config |
| Technicals | MCP tool `analyze_technicals(stock_code)` served by `market-analyzer-mcp` | TA-Lib and QuestDB stay on the Python side |
| Weights | Relative; code normalizes holdings + cash to sum to 1 | The LLM should not do arithmetic |
| Validation failure | Returned to the agent as the tool result; it retries until valid or `MAX_TURNS` | One run, self-correcting |
| Schema | Alembic migration `0005` in Python, like every other Postgres table | Single migration owner |

### Service-to-service exception

`portfolio-builder → market-analyzer-mcp` (MCP over Streamable HTTP) is the **only** direct
service call in the system. Every other pair still communicates through datastores.
AGENTS.md records this exception.

### Table ownership

`portfolio-builder` writes `portfolios`, `portfolio_holdings`, `portfolio_exits`. It reads
`clusters`, `article_clusters`, `articles`, `cluster_summaries`, `entities`,
`cluster_entities`, `relations`, `companies`, `company_aliases`, `themes`,
`theme_companies`. It never touches QuestDB.

## 3. Schema (migration `0005`)

```
portfolios
  id            bigint identity PK
  created_at    timestamptz not null default now()
  cash_weight   double precision not null
  commentary    text not null        -- the agent's overall assessment and decisions this run
  model         text not null        -- e.g. openai-codex/gpt-5.x-codex

portfolio_holdings
  portfolio_id       bigint FK portfolios(id) ON DELETE CASCADE
  company_id         text not null FK companies(corp_code)
  weight             double precision not null
  reason             text null       -- required when the company is entering (§5.5)
  cited_cluster_ids  bigint[] not null default '{}'
  PK (portfolio_id, company_id)

portfolio_exits
  portfolio_id       bigint FK portfolios(id) ON DELETE CASCADE
  company_id         text not null FK companies(corp_code)
  reason             text not null
  cited_cluster_ids  bigint[] not null default '{}'
  PK (portfolio_id, company_id)
```

Index `portfolios_created_at_idx` on `portfolios(created_at)`.

`company_id` is `companies.corp_code`, the table's primary key; the FK rejects an unknown
company at insert. `cited_cluster_ids` cannot carry an FK (it is an array), so
`save_portfolio` checks the cited ids against `clusters` inside the same write transaction
(§5.5).

Migration `0005` also adds a GIN index on `cluster_summaries` for full-text search:

```sql
CREATE INDEX cluster_summaries_fts_idx ON cluster_summaries
  USING gin (to_tsvector('simple', title || ' ' || summary));
```

`cluster_summaries` belongs to news-graph-builder; an index does not change what it writes.

## 4. Run flow

1. Load settings; set up logging; open the credential store (§8).
2. **Ingestion** (`portfolio/briefing.ts`):
   - previous portfolio with its holdings, exits and commentary (none on the first run);
   - `cluster_summaries` of clusters whose `updated_at` falls within the last
     `NEWS_WINDOW_DAYS` days;
   - for each cluster, the companies it mentions
     (`cluster_entities → entities.corp_code → companies`), each shown with its
     `company_id` (`corp_code`), name and `stock_code`;
   - for each such company, its themes (`theme_companies → themes`), with `is_main`.
3. Render the system prompt (rules) and the briefing (one user message).
4. Run the agent with the tools in §5 until `submit_portfolio` succeeds (it returns
   `terminate: true`) or the turn count reaches `MAX_TURNS`.
5. Exit 0 on a saved portfolio; exit non-zero on `MAX_TURNS`, an LLM failure or an
   ingestion failure. Nothing is written unless `submit_portfolio` succeeded.

### System prompt

**Goal.** The agent is the portfolio manager of one model portfolio of KOSPI stocks that
every user of the service follows. Each run it reviews the previous portfolio against what
has happened in the news since, and decides what to hold, at what relative weight, what to
drop and how much to keep in cash — then submits the new portfolio with its reasons.

**Grounding rule.** The portfolio must carry reasons, and every reason must originate in
the briefing or in a tool result from this run — a news cluster (cited by `cluster_id`), a
graph relation or a technical analysis. The agent may use its pre-trained knowledge while
thinking (to interpret events, relate industries, decide what to look up), but a fact it
knows only from memory cannot be the basis of a stored reason; it must first find that fact
in the data with a tool.

**Rules.**

- Weights are relative and non-negative; the system normalizes them with cash to sum to 1.
- Every company not in the previous portfolio needs a `reason`.
- Every previous holding that is dropped needs an `exits` entry with a `reason`.
- Cite the `cluster_id`s a decision relies on.
- Write a `commentary` covering the portfolio as a whole and this run's decisions.
- Validation errors come back as the tool result; fix them and submit again.

## 5. Tools (`src/tools/`)

Each tool is one file exporting one `AgentTool` with a TypeBox schema. Tool errors
(MCP down, query failure) are returned to the agent as error text; they never end the run.

### 5.1 `analyze_technicals(name)`

1. Normalize `name` with `normalize_company_name` — a port of news-graph-builder's
   `common/normalize.py`: NFKC, strip `(주)`, `㈜`, `주식회사`, remove whitespace, lowercase.
2. `company_aliases.alias = normalized → corp_code → companies.stock_code`. Accepting a
   `company_id` directly is also allowed (`corp_code` match first).
3. Unresolved: return an error listing up to 5 candidates from
   `companies.corp_name ILIKE '%' || name || '%'`.
4. Resolved: call MCP `analyze_technicals(stock_code)` and return its text content to the
   agent, headed by the resolved `company_id` and name. The log records the resolved
   `company_id` and `stock_code`.

Aliases are stored normalized by Python, so the two normalizers must agree: a shared case
file (`services/portfolio-builder/tests/fixtures/normalize_cases.json`) is asserted by both
a Bun test and a news-graph-builder pytest.

### 5.2 `get_news_cluster(id)`

The cluster's title and summary, member article titles with `published_at` and source,
its entities, and its relations (source, type, target, description).

### 5.3 `search_news_cluster(query)`

Full-text search over `cluster_summaries`. The query is split on whitespace, each term
escaped and turned into a prefix term (`삼성:*`) combined with `&`, matched against
`to_tsvector('simple', title || ' ' || summary)`, ranked by `ts_rank`, top 10.
Returns `cluster_id`, title, a summary excerpt and rank. Prefix terms exist because Korean
particles attach to words: without them `삼성전자가` never matches `삼성전자`.

### 5.4 Graph tools

`entities` are nodes and `relations` are directed, typed edges, each carrying the
`cluster_id` it came from. Both tools traverse edges in either direction with recursive
CTEs (Postgres `CYCLE` clause, so no node repeats on a path). **Seed entities** for a name
are those whose `name` contains the normalized query, plus the company entity whose
`corp_code` the name resolves to through `company_aliases`.

**`search_graph(name, depth = 2)`** — the neighbourhood of the seed entities up to `depth`
hops (1–3). Returns the subgraph: nodes (id, name, type, `company_id` for companies, hop
distance) and edges (source, type, target, description, `cluster_id`), ordered by hop. No
edge cap; the depth bound limits the result, so the agent picks a smaller depth for hub
entities. Both graph tools run under a 10 s `statement_timeout`; a timeout returns an error
asking for a smaller depth or a more specific name, and never truncates a result.

**`find_graph_paths(from_name, to_name, max_depth = 4)`** — every simple path of at most
`max_depth` edges (1–6) between any seed of `from_name` and any seed of `to_name`, shortest
first. Each path is the node sequence with the edge (type, direction, description,
`cluster_id`) between each pair. Answers questions like "how does this event reach that
company".

Unmatched names return an error with up to 5 candidate entity names.

### 5.5 `submit_portfolio(...)`

```
{
  holdings:   [{ company_id, weight, reason?, cited_cluster_ids }],
  exits:      [{ company_id, reason, cited_cluster_ids }],
  cash_weight,
  commentary
}
```

`portfolio/validate_portfolio.ts` is pure — it reads nothing from the database beyond the
previous portfolio already loaded at ingestion — and collects **every** error before
answering:

- no duplicate `company_id` within holdings or exits; no company both held and exited;
- every weight and `cash_weight` is ≥ 0, and their total is > 0;
- every holding absent from the previous portfolio has a non-empty `reason`;
- every previous holding absent from `holdings` appears in `exits`; every exit was a
  previous holding; every exit `reason` is non-empty;
- `commentary` is non-empty.

Valid → `portfolio/normalize_weights.ts` divides each weight and cash by their total
(0.1, 0.1, cash 0.05 → 0.4, 0.4, 0.2) → `portfolio/save_portfolio.ts` writes in one
transaction, where existence is verified:

1. insert `portfolios`, then `portfolio_holdings` and `portfolio_exits` — an unknown
   `company_id` fails the FK;
2. check every cited cluster id against `clusters` in the same transaction;
3. commit.

Any failure rolls back and is turned into agent-readable errors (the FK violation names
the offending `company_id`; missing clusters are listed). Invalid or failed write → the
error list is the tool result and the agent continues. Success → the tool returns the
portfolio id with `terminate: true`.

## 6. Logging (`src/log.ts`)

JSON lines on stdout, one object per event, every line carrying `run_id`, `ts`, `level`,
`event`. Level from `PORTFOLIO_BUILDER_LOG_LEVEL`.

| Event | Fields |
|---|---|
| `run_start` | provider, model, reasoning level, temperature (`null` when not sent), max turns, news window |
| `ingestion` | previous portfolio id, cluster ids and count, company count, theme count, previous holdings/exits, briefing length in characters |
| `prompt` | full system prompt and full briefing, once per run |
| `llm_request` | per turn, from `onPayload`: model, temperature, reasoning effort, tool names, message count — not message bodies, which repeat the whole context every turn |
| `llm_response` | per turn: assistant text, reasoning summary if returned, tool calls, stop reason, latency, usage `input`, `output`, `cache_read`, `cache_write`, cost |
| `tool_call` | name, args, result text, duration, error flag; `analyze_technicals` adds the resolved `company_id` and `stock_code` |
| `validation_failed` | the error list sent back to the agent |
| `run_end` | outcome (`saved` / `max_turns` / `error`), portfolio id, turns, usage totals, elapsed |

Tokens and credentials are never logged.

## 7. Code, configuration and packaging

### Layout (`services/portfolio-builder/`)

```
package.json  bun.lock  tsconfig.json  biome.json
src/
  main.ts  settings.ts  log.ts
  agent.ts         -- builds the Pi Agent, turn limit, event logging
  credentials.ts   -- file CredentialStore + openai-codex Models
  pg_array.ts      -- Postgres array literal for ANY(...) parameters
  portfolio/
    briefing.ts  validate_portfolio.ts  normalize_weights.ts  save_portfolio.ts
  tools/
    analyze_technicals.ts  normalize_company_name.ts  get_news_cluster.ts
    search_news_cluster.ts  graph_seeds.ts  search_graph.ts  find_graph_paths.ts
    submit_portfolio.ts
tests/
```

Dependencies: `@earendil-works/pi-agent-core`, `@earendil-works/pi-ai`,
`@modelcontextprotocol/sdk`, `typebox`. Dev: `typescript`, `@biomejs/biome`,
`@types/bun`. Postgres through `Bun.sql`; tests through `bun test`.

The Python stub is removed: `services/portfolio-builder` leaves the uv workspace members,
`tach.toml`, the CI Python loops (`verify-exported-requirements`, isolation) and
`docker/requirements/portfolio-builder.txt`; `uv.lock` is regenerated.

### Settings (`PORTFOLIO_BUILDER_` prefix)

| Variable | Default |
|---|---|
| `POSTGRES_DSN` | required |
| `MARKET_MCP_URL` | required (Streamable HTTP endpoint of market-analyzer-mcp) |
| `LLM_MODEL` | required (an `openai-codex` model id, e.g. `gpt-5.5`) |
| `THINKING_LEVEL` | `medium` (`off`, `minimal`, `low`, `medium`, `high`, `xhigh`) |
| `OPENAI_ACCESS_TOKEN` | seeds the credential store when it is empty |
| `OPENAI_REFRESH_TOKEN` | seeds the credential store when it is empty |
| `OPENAI_TOKEN_EXPIRES_EPOCH` | seeds the credential store when it is empty (epoch ms) |
| `CREDENTIALS_PATH` | `/data/auth.json` |
| `NEWS_WINDOW_DAYS` | `7` |
| `MAX_TURNS` | `150` |
| `LOG_LEVEL` | `INFO` |

`QUESTDB_DSN` and `NEWS_CLUSTERER_URL` are removed.

### Docker and compose

`docker/portfolio-builder.Dockerfile`: `oven/bun` base, `bun install --frozen-lockfile
--production`, copy `src/`, `CMD ["bun", "src/main.ts"]`. Build context stays the repo root.

`compose.dev.yaml`: `portfolio-builder` in the `jobs` profile, depends on healthy postgres,
volume `portfolio-builder-data:/data`, env passed through (tokens from `.env`).

### CI

`ci-dev.yaml` gains a `portfolio-builder` job: `setup-bun`, `bun install --frozen-lockfile`,
`biome check`, `tsc --noEmit`, alembic migrations against the Postgres service, `bun test`.
The image build matrix keeps `portfolio-builder`.

### Documentation

AGENTS.md and README: the TS member and its commands, the new env table, the MCP exception.

## 8. Credentials and accepted risk

pi-ai refreshes `openai-codex` access tokens automatically through a `CredentialStore`, and
OpenAI rotates the refresh token on use. A token held only in env would die after the first
refresh, so the store is a JSON file at `CREDENTIALS_PATH` on a persistent volume, seeded
from the `OPENAI_*` variables only while empty. The file is mode `0600`.

**Accepted risk (confirmed 2026-09-27):** a personal ChatGPT subscription token lives on the
server, and subscription access may not be intended to back an automated service. The
fallback is an OpenAI API key through pi-ai's `openai` provider — a settings change, not a
redesign.

## 9. Contract with `market-analyzer-mcp`

- Transport: MCP Streamable HTTP at `MARKET_MCP_URL`.
- Tool: `analyze_technicals(stock_code: string)` → text content describing the stock's
  indicators and verdicts. portfolio-builder passes the text to the agent unchanged.
- Unknown stock or missing data → an MCP tool error, which is relayed to the agent.

## 10. Testing

- Unit: `normalize_weights`; every `validate_portfolio` rule, including first run (no
  previous portfolio); `normalize_company_name` against the shared case file.
- Tools against a Postgres test database (`KTB_TEST_POSTGRES_DSN`, skipped when unset):
  alias resolution and candidates, `search_news_cluster` prefix matching, `search_graph`
  depth bounds and cycle safety, `find_graph_paths` ordering on a small fixture graph,
  `get_news_cluster`; `save_portfolio` atomicity, unknown `company_id` and unknown cited
  cluster each rolling back with a readable error.
- `analyze_technicals` against an in-process MCP server from `@modelcontextprotocol/sdk`.
- End-to-end: the agent with a scripted stream function calls tools, submits an invalid
  portfolio, receives the errors, submits a valid one, and one version is saved; a second
  script never submits and the run ends with `max_turns` and nothing written.

## 11. Limits

- `simple` full-text search has no Korean morphology; prefix terms cover particles but not
  compound splitting.
- One global model portfolio; no per-user state.
- `analyze_technicals` quality depends entirely on `market-analyzer-mcp`.
- Graph results are unbounded within the depth limit; a hub entity at depth 3 can return a
  large subgraph. The 10 s statement timeout turns a runaway query into a tool error.
- The grounding rule is enforced by the prompt, not checked in code, apart from cited
  `cluster_id`s having to exist at write time.
