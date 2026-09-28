# portfolio-builder — LangChain Agent in Python

**Date:** 2026-09-28
**Status:** Draft for review
**Supersedes:** the runtime sections of `2026-09-27-portfolio-builder-design.md` (TypeScript, Pi,
`openai-codex`, market-analyzer-mcp). That spec and its implementation live only on branch
`claude/typescript-pi-langchain-migration-e24c71` and were never merged; this service ports its
behaviour, not its code.

## 1. Purpose and scope

`portfolio-builder` replaces the Python stub on `dev`. It runs once per cron trigger, builds **one
model portfolio** with a LangChain agent (`create_agent`), stores it as a new version, and exits.
Every user receives the same model portfolio.

The agent starts from a code-assembled briefing (previous portfolio, recent news clusters, the
KOSPI companies they mention, those companies' themes) and drills down with tools: news clusters,
the knowledge graph, and technical evidence computed from QuestDB OHLCV with TA-Lib. It must
justify every stock that enters or leaves.

### Behaviour kept from the TypeScript design

Tables, validation rules, weight normalisation, the briefing, the grounding rule and system
prompt (except its tools line, which now describes `analyze_technicals(name, timeframe)` as
returning technical evidence for the chosen timeframe), the six tools, the nudge when the model stops without submitting, the `MAX_TURNS` limit,
the run outcomes (`saved` / `max_turns` / `error`) and the JSON log events.

### Non-goals

- The work queue. `portfolio-rebalancer-http` consumes it, after the MVP. portfolio-builder is a
  cron job.
- market-analyzer-mcp. portfolio-builder computes technical evidence itself.
- `ktb-market-analyzer`. portfolio-builder no longer depends on it and calls TA-Lib directly.
- Collecting KOSPI index bars, industry codes, or moving the theme sync into market-collector.
  That is a separate spec (§10); this service returns `null` for evidence that needs it.
- Per-user portfolios, hard portfolio constraints, deployment mechanics.

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Language | Python 3.13, uv member (the existing `services/portfolio-builder`) | Same toolchain as every other service; TA-Lib is in-process |
| Agent | `langchain.agents.create_agent` + middleware | Each Pi `finishTurn` duty maps to a documented primitive (§4) |
| LLM | `langchain_openrouter.ChatOpenRouter`, key from `PORTFOLIO_BUILDER_OPENROUTER_API_KEY` | Team choice; no OAuth refresh or credential volume |
| Reasoning | `reasoning={"effort": THINKING_LEVEL}` | OpenRouter's normalised effort levels |
| Turn limit | `ModelCallLimitMiddleware(run_limit=MAX_TURNS, exit_behavior="error")` | The limit is a signal (`ModelCallLimitExceededError`), not a count inferred afterwards |
| Stop on save | `submit_portfolio` writes `portfolio_id` into agent state; a `before_model` hook jumps to `end` | Typed state, no parsing of message text |
| Postgres | SQLAlchemy Core + psycopg, `sa.Table` mirrors, like the news services | Existing pattern |
| QuestDB | Official `questdb` client, `questdb.connect(conf)` → `db.query(sql, params)` | AGENTS.md rule; same as market-collector |
| Technicals | TA-Lib primitives over OHLCV into a fixed evidence set (§6) | Economically interpretable evidence instead of oscillator verdicts |
| Timeframe | Chosen by the agent per call (`1m`, `15m`, `1h`, `1d`); never fixed in code | The agent decides the horizon it is reasoning about |
| Company-name normaliser | Moves from `news_graph_builder.common` to `ktb_core.normalize` | Two Python callers now exist |

`portfolio-builder` writes `portfolios`, `portfolio_holdings`, `portfolio_exits`. It reads
`clusters`, `article_clusters`, `articles`, `cluster_summaries`, `entities`, `cluster_entities`,
`relations`, `companies`, `company_aliases`, `themes`, `theme_companies` in Postgres, and
`bars_1m`, `bars_15m`, `bars_1h`, `bars_1d`, `universe_members` in QuestDB. It never writes
QuestDB.

## 3. Layout

```
services/portfolio-builder/
  pyproject.toml          ktb-core, langchain, langchain-openrouter, sqlalchemy, psycopg[binary],
                          questdb, ta-lib, numpy, pydantic-settings
  src/portfolio_builder/
    __main__.py           main(): settings → log → engines → briefing → run_agent → exit code
    settings.py           PORTFOLIO_BUILDER_ prefix
    database.py           Postgres engine and sa.Table mirrors of the tables above
    briefing.py           SYSTEM_PROMPT and load_briefing
    agent.py              create_agent, the middleware (§4), run_agent → RunResult
    portfolio.py          validate, normalize_weights, save (one transaction, SaveError)
    evidence.py           OHLCV arrays → evidence dict (pure, TA-Lib + numpy)
    market.py             QuestDB reads: candles(symbol, timeframe), universe closes
    tools/
      news.py             get_news_cluster, search_news_cluster
      graph.py            search_graph, find_graph_paths
      technicals.py       analyze_technicals
      submit.py           submit_portfolio
  tests/
infrastructure/postgres/migrations/versions/0005_create_portfolios.py   ported from the TS branch
packages/core/src/ktb_core/normalize.py                                  moved
```

Tools are closures over their dependencies (engine, market reader, previous holdings, log), so
tests build them without globals.

## 4. Agent loop

```python
class PortfolioState(AgentState):
    portfolio_id: NotRequired[int]

agent = create_agent(
    model=ChatOpenRouter(model=s.llm_model, api_key=s.openrouter_api_key,
                         reasoning={"effort": s.thinking_level}),
    tools=tools,
    system_prompt=SYSTEM_PROMPT,
    state_schema=PortfolioState,
    middleware=[
        RunLog(log),
        ToolErrorMiddleware(on_tool_error),
        StopOnSave(),
        Nudge(),
        ModelCallLimitMiddleware(run_limit=s.max_turns, exit_behavior="error"),
    ],
)
final = agent.invoke({"messages": [HumanMessage(briefing.text)]},
                     {"recursion_limit": RECURSION_LIMIT})
```

- **StopOnSave**: `before_model`, `can_jump_to=["end"]`. When `portfolio_id` is in state, jump to
  `end`. It runs before the call-limit check, so a save on the last allowed turn is `saved`.
- **Nudge**: `after_model`, `can_jump_to=["model"]`. When the newest `AIMessage` has no
  `tool_calls` (a normal stop or a truncated one), append `HumanMessage(NUDGE)` and jump to
  `model`. NUDGE is the TS text: "You stopped without a saved portfolio. Keep investigating with
  the tools if you need to, then call submit_portfolio."
- **ToolErrorMiddleware**: turns only the expected exceptions into an error `ToolMessage` the model
  can act on: `PortfolioRejected`, `UnknownCompany`, `NoMarketData`, `GraphTimeout`. Anything else
  propagates and the run ends as `error`.
- **submit_portfolio**: validates, normalises and saves, then returns
  `Command(update={"portfolio_id": id, "messages": [ToolMessage(f"Saved portfolio {id}.", ...)]})`.
  ToolNode runs one message's tool calls concurrently in a thread pool, so the tool holds a
  `threading.Lock` and a `saved_id`; a second call gets "portfolio already saved as N; the run is
  over".
- **Sync end to end.** `agent.invoke`, `wrap_model_call` / `wrap_tool_call`, the sync SQLAlchemy
  engine and the sync `questdb` client, like every other service. No `asyncio` anywhere.
- **recursion_limit.** Every middleware hook is its own graph node, so one turn is roughly 5–6
  steps. `RECURSION_LIMIT` is derived from the node count of the built graph with headroom
  (`steps_per_turn * MAX_TURNS * 2`), so `GraphRecursionError` can never fire before
  `ModelCallLimitExceededError`. The implementation measures `steps_per_turn` from
  `agent.get_graph()` and a test asserts the limit ends the run as `max_turns`, not `error`.

### Outcomes

| Condition | Outcome | Exit code |
|---|---|---|
| `portfolio_id` in the final state | `saved` | 0 |
| `ModelCallLimitExceededError` raised | `max_turns` | 1 |
| Any other exception, or the run ends without `portfolio_id` | `error` | 1 |

The exception path returns no final state, so `turns` and usage totals for `run_end` come from
**RunLog's counters**, not from state.

### Logging

`ktb_core.logging.setup_logging()` first, JSON on stdout, one `run_id` per run. Event names and
fields match the TS service:

| Event | Source | Fields |
|---|---|---|
| `run_start` | main | provider `openrouter`, model, reasoning_level, max_turns, news_window_days |
| `ingestion` | main | previous_portfolio_id, previous_holdings/exits, cluster_ids, counts, briefing_chars |
| `prompt` | main | system_prompt, briefing |
| `llm_request` | RunLog `wrap_model_call` | turn, tool names, message_count |
| `llm_response` | RunLog `wrap_model_call` | turn, model, finish_reason, text, reasoning, tool_calls, latency_ms, usage |
| `tool_call` | RunLog `wrap_tool_call` | turn, name, args, result, is_error, duration_ms (WARNING on error) |
| `validation_failed` | submit_portfolio | errors (WARNING) |
| `run_end` | main | outcome, portfolio_id, turns, usage totals, error, elapsed_ms (ERROR unless saved) |

`usage` comes from `usage_metadata`: input, output, cache_read, reasoning, total. `cost` is logged
only if `ChatOpenRouter` exposes OpenRouter's cost in `response_metadata`; it is never estimated.

`ktb_core.logging` gains a redaction step for OpenRouter keys (`sk-or-…`), `*_token` JSON fields
and JWT-shaped strings, so an LLM error that echoes a header cannot leak it. The key is a
`SecretStr` in settings.

## 5. Tools (ported)

| Tool | Behaviour |
|---|---|
| `get_news_cluster(id)` | One cluster: summary and its articles |
| `search_news_cluster(query)` | Postgres FTS (`simple`, prefix terms) over cluster summaries |
| `search_graph(name, depth=2)` | Entities within `depth` (1–3) hops, and relations among them with `cluster_id`, nearest first; recursive CTE |
| `find_graph_paths(from, to)` | Shortest relation paths between two entities; revisit guard |
| `analyze_technicals(name, timeframe)` | §6 |
| `submit_portfolio(holdings, exits, cash_weight, commentary)` | Validate, normalise, save (§4) |

Graph queries run under a 10 s `statement_timeout`; a timeout becomes `GraphTimeout`. Names
resolve through `companies.corp_code` first, then `company_aliases.alias` via
`ktb_core.normalize`; no match raises `UnknownCompany` listing up to five `ILIKE` candidates.
Tool arguments are Pydantic models; field descriptions carry over from the TS schemas.

### Validation (unchanged)

- Weights ≥ 0; `cash_weight` ≥ 0; the total must be positive.
- A company entering the portfolio needs a non-empty reason.
- No duplicate holdings or exits; nothing both held and exited.
- An exit must be a previous holding with a reason; every dropped previous holding needs an exit.
- Commentary must not be empty.
- On save, the `company_id` FK and the check that every cited cluster exists run in the same
  transaction; a failure rolls back and returns a readable error.

Weights are normalised so holdings plus cash sum to 1. `portfolios.model` stores
`openrouter/<LLM_MODEL>`.

## 6. `analyze_technicals(name, timeframe)`

`timeframe` is a required enum, `1m | 15m | 1h | 1d`, with no default. The description tells the
agent what each horizon is for. The enum maps to a whitelisted view name; the value never reaches
SQL as text.

### Reads (`market.py`)

```sql
SELECT ts, high, low, close, volume FROM bars_<tf>
WHERE symbol = $1 [AND session = 'regular']   -- 1m and 1d only; 15m/1h views have no session
ORDER BY ts DESC LIMIT $2
```

- Rows are reversed to oldest-first numpy float64 arrays.
- `LIMIT` is 300 for every timeframe. The longest need is 273 daily bars: a 20-bar volatility
  (21 closes) ranked against the previous 252 values. Evidence that lacks history is `null` with
  the number of bars it needs.
- The symbol is the company's `stock_code`. No rows means `NoMarketData`: the company is not in
  the KOSPI 200 archive, or nothing has been collected.
- Cross-section inputs are the daily closes of the latest `universe_members` snapshot, read once
  per run and cached in the tool's closure.

### Evidence (`evidence.py`, pure)

The output is JSON: company, timeframe, `as_of` (newest `ts`), `bars` (count), and each evidence
value. A `null` always comes with a `reason`.

**Daily (`1d`): the full set**

| Evidence | Calculation |
|---|---|
| `return_5d` | `ROCP(close, 5)` |
| `return_20d` | `ROCP(close, 20)` |
| `return_60d` | `ROCP(close, 60)` |
| `market_excess_return_5d` | `return_5d − market_return_5d`. `null` until KOSPI index bars exist (§10) |
| `industry_excess_return_5d` | `return_5d − industry_return_5d`. `null` until industry codes exist (§10) |
| `return_5d_cross_section_percentile` | Percentile of `return_5d` among the latest KOSPI 200 snapshot |
| `momentum_12m_skip1m` | `close[t−21] / close[t−252] − 1` |
| `momentum_cross_section_percentile` | Percentile of `momentum_12m_skip1m` among KOSPI 200 |
| `ma_gap_20_60` | `SMA(close,20) / SMA(close,60) − 1` |
| `distance_to_prev_20d_high` | `close / MAX(high, 20)[t−1] − 1` (previous 20 bars, excluding today) |
| `breakout_20d` | `close > MAX(high, 20)[t−1]` |
| `price_to_52w_high` | `close / MAX(close, 252)[t−1]` |
| `realized_volatility_20d` | `STDDEV(ROCP(close,1), 20)` |
| `volatility_percentile_1y` | `PERCENTRANK(realized_volatility_20d, 252)` |
| `relative_volume_20d` | `volume / SMA(volume, 20)` |
| `amihud_illiquidity_20d` | Mean over 20 bars of `abs(ROCP(close,1)) / (close × volume)` |
| `amihud_percentile_1y` | `PERCENTRANK(amihud_illiquidity_20d, 252)` |

A zero-volume bar (trading halt, a quiet minute) makes `relative_volume` and Amihud divide by
zero; any non-finite result is `null` with reason "zero volume", never `inf` or `NaN`.

Traded value is approximated as `close × volume` because `bars` stores OHLCV only.
`turnover_20d` and `relative_turnover` are omitted: shares outstanding is not stored anywhere.

**Intraday (`1m`, `15m`, `1h`): the scale-free subset, windows in bars**

`return_5`, `return_20`, `return_60`, `ma_gap_20_60`, `distance_to_prev_20_high`, `breakout_20`,
`realized_volatility_20`, `relative_volume_20`, all computed as the daily formulas above over bars
of the chosen timeframe. Cross-section, momentum, 52-week, 1-year percentile and Amihud evidence is
daily only.

RSI, MACD/PPO, STOCH/WILLR, ADX, OBV/AD/ADOSC and candlestick patterns are excluded. `TRANGE` and
`NATR` stay optional and are not in the first version.

## 7. Settings (`PORTFOLIO_BUILDER_`)

| Variable | Default |
|---|---|
| `POSTGRES_DSN` (`postgresql+psycopg://`) | required |
| `QUESTDB_CONF` (e.g. `ws::addr=questdb:9000;`) | required |
| `OPENROUTER_API_KEY` | required |
| `LLM_MODEL` (OpenRouter model id) | required |
| `THINKING_LEVEL` (`none`/`minimal`/`low`/`medium`/`high`/`xhigh`) | `medium` |
| `NEWS_WINDOW_DAYS` | `7` |
| `MAX_TURNS` | `150` |
| `LOG_LEVEL` | `INFO` |

The stub's `QUESTDB_DSN` and `NEWS_CLUSTERER_URL` are removed.

## 8. Packaging, repo changes, docs

- `pyproject.toml` drops `ktb-market-analyzer` and adds the §3 dependencies; `tach.toml` sets
  `portfolio_builder.depends_on = ["ktb_core"]`.
- The normaliser moves to `ktb_core.normalize`. news-graph-builder imports it from there, and
  `tach.toml` drops `normalize` from the `news_graph_builder.common` interface. The TS branch's
  `normalize_cases.json` becomes a `ktb_core` test.
- Migration `0005` (portfolios, holdings, exits, `portfolios_created_at_idx`, the cluster FTS
  index) and its metadata test are ported from the TS branch.
- Run `uv lock`, then
  `uv export --package portfolio-builder --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/portfolio-builder.txt`.
- The Dockerfile follows the Python pattern and gets the TA-Lib C library the same way the current
  image does.
- compose: a `portfolio-builder` job in the `jobs` profile, depending on healthy postgres and
  questdb, with env passed through and the key from `.env`.
- AGENTS.md and README: the member row ("cron: runs once and exits"); the work-queue line
  ("no consumer yet; portfolio-rebalancer-http after the MVP"); the env table; the market-analyzer
  dependency removed.
- The 09-27 spec is brought over with a header pointing here.

## 9. Testing

- **Pure:** `validate` (every rule, including a first run with no previous portfolio),
  `normalize_weights`, settings (required, invalid, defaults), log redaction, `normalize` against
  the shared cases.
- **Evidence:** synthetic arrays with known answers for every formula, including null-with-reason
  when history is short, the `[t−1]` exclusion in the breakout and 52-week evidence, and a
  zero-volume bar yielding `null` rather than `inf`/`NaN`.
- **Technicals tool:** an injected market reader. Each timeframe maps to its view, `session` is
  filtered only for 1m/1d, the intraday subset has no daily-only keys, an unknown company raises
  `UnknownCompany`, and a symbol with no rows raises `NoMarketData`. No test writes QuestDB.
- **Postgres** (`KTB_TEST_POSTGRES_DSN`, skipped when unset), ported from the TS suite: alias
  resolution and candidates, FTS prefix search, `search_graph` depth and cycle safety,
  `find_graph_paths` ordering, graph timeout, and `save` atomicity for an unknown company and an
  unknown cited cluster.
- **Agent end-to-end** with a scripted fake chat model (`GenericFakeChatModel` with `bind_tools`
  returning itself; verified against the installed version):
  1. An invalid submit gets its errors back, the corrected submit saves one row, outcome `saved`.
  2. A model that never submits is nudged and ends as `max_turns` (not `error`) with nothing
     written, run with a small `MAX_TURNS` and the real `RECURSION_LIMIT`.
  3. A submit on exactly turn N with `run_limit=N` is `saved`.
  4. Two `submit_portfolio` calls in one message, through the real ToolNode, write one row; the
     second gets "already saved".
  5. An unexpected tool exception ends as `error`.
  6. The log contains `llm_request`, `llm_response`, `tool_call`, `validation_failed`, `run_end`.

## 10. Follow-up spec: market-collector data

These change market-collector and news-graph-builder, and get their own spec:

- Collect KOSPI index daily bars into QuestDB. This enables `market_excess_return_5d`.
- Store an industry code per company in Postgres. This enables `industry_excess_return_5d`,
  computed as the equal-weight mean `return_5d` of KOSPI 200 constituents in the same industry.
- Move the Kiwoom theme sync (`themes`, `theme_companies`) from news-graph-builder to
  market-collector, writing Postgres (QuestDB theme collection is already removed).

Until then, the two excess-return values are `null` with reason "benchmark data not collected".
portfolio-builder's briefing reads `themes` / `theme_companies` whichever service writes them.

## 11. Limits

- `bars_15m` and `bars_1h` are materialised views over all 1m rows, extended session included, so
  intraday evidence on those timeframes mixes sessions. Tracked in #47; this service does not work
  around it.
- Cross-section percentiles cover KOSPI 200 only, the collected universe.
- `momentum_12m_skip1m` and the 1-year percentiles are `null` until 273 regular daily bars exist
  for the symbol.
- `simple` FTS has no Korean morphology.
- The grounding rule is enforced by the prompt, apart from cited clusters having to exist.
- `langchain.agents` middleware APIs are recent (1.x); pin the versions in `uv.lock`.
