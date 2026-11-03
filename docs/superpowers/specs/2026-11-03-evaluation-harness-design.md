# Evaluation harness: information, decisions, records, sequencing, and Compose

Date: 2026-11-03
Status: Implemented
Issues: #435, #436, #437, #438, #439, #475 (parent #433). Builds on
[`2026-10-31-investment-harness-loop-design.md`](2026-10-31-investment-harness-loop-design.md)
(#434) and [`2026-10-31-evaluation-market-data-contract-design.md`](2026-10-31-evaluation-market-data-contract-design.md).

## Contracts (`harness.contracts`)

```python
class InformationProvider(Protocol):
    capabilities: frozenset[str]
    manifest: Mapping[str, object]
    def open(self, as_of: datetime) -> InformationSession: ...

class InformationSession(Protocol):
    as_of: datetime
    tools: tuple[HttpTool, ...]            # HTTP representation for tool-calling agents
    def daily_bars(self, symbol, limit) -> list[DailyBar]: ...        # direct representation
    def news(self, days, query, limit) -> list[NewsArticle]: ...
    def technicals(self, symbol) -> Mapping[str, float | None]: ...

class DecisionSystem(Protocol):
    requires: frozenset[str]
    manifest: Mapping[str, object]
    def decide(self, context, information, audit: dict) -> PortfolioProposal | Hold: ...
```

- The #434 `decide(context)` gains the information session and a harness-owned `audit` dict.
  Raw responses, probabilities, tool calls and costs go to `audit`; the proposal stays target
  weights plus cash.
- `Hold` means no new orders; pending orders stay. It makes "decided, did not trade"
  explicit, and Buy-and-Hold uses it.
- `DecisionFailed(kind, detail)` with kinds `no_submission`, `invalid_submission`, `timeout`,
  `tool_error`, `error`. The loop records any other exception as `error` and a failed
  `information.open` as `tool_error`. A failed decision places no orders and the episode
  continues.
- The runner rejects a decision system whose `requires` is not covered by the provider's
  `capabilities` before any episode (`unsupported`, exit 4). Nothing falls back silently.

## Point-in-time information (#435)

- Clock: `ktb_core.clock_file` writes an ISO timestamp atomically. The harness writes it
  before each Decide; servers read it per request. The clock directory is a per-run tmpfs
  volume, mounted read-only by PIT servers and not mounted by decision containers.
- `market-http-evaluation` (new console script in market-http) serves only
  `GET /bars/{code}?timeframe=1d` and `/health`. With `MARKET_HTTP_EVALUATION_ACCESS=pit` the
  query adds `timestamp <= last_closed_date(clock)`; `harness` access has no cutoff and is
  for the harness network only. The two modes are explicit and mutually exclusive with the
  clock setting.
- `news-http-evaluation` serves `GET /news/articles?days&q&limit&cursor`: raw articles with
  `as_of - days < published_at <= as_of`, plus `fetched_at <= as_of` under `fetched`
  availability. It follows the 2026-10-28 boundary in the
  [투자 벤치마크](https://app.notion.com/p/406b24f4c803809db24dd89f3654921c) notes: the common
  news API serves raw articles, keyword search may name companies or themes, and cluster,
  graph and cluster-based `/news/recent` stay out of it. The 2026-10-31 contract's mention of
  `/news/recent` is superseded for the same reason: cluster summaries are recomputed over all
  articles, so a past `as_of` would still see later ones. Cluster and graph variants need
  point-in-time rebuilt derived data first.
- Clients cannot move the cutoff: unknown parameters are ignored, cursors only narrow the
  range, other timeframes are 422, other routes 404, and an unreadable clock is 503.
- Variants: `none`, `market`, `raw` (market + news), `enriched` (raw + `features`). The
  `features` server computes returns, volatility, moving-average gaps, RSI and volume ratio
  from the PIT market server only. Manifests list variant, version, capabilities, endpoints,
  and tool names.

## Decision systems (#436)

- In-process: `EqualWeight`, `BuyAndHold`, and `DecisionModelAdapter`. The adapter is the
  Jev-shaped pipeline: a feature builder over PIT bars, then per-symbol BUY/HOLD/SELL
  probabilities, a threshold, and an allocation. Its versioned policy goes in the manifest
  and its probabilities go in `audit`. `momentum_choices` is the stub model.
- Docker Agent: the runner hosts a submission desk (`GET/POST /portfolio`, keyed by an
  `X-Decision-Id` header). The `agent-worker` container renders a Docker Agent config.
  Each information tool and the two desk tools become an `api` toolset, and the model comes
  from settings. The worker runs `docker-agent run --exec --yolo` in a fresh HOME with a
  timeout.
- Desk rules: one accepted portfolio per decision. Invalid payloads get 422 with every error
  and may be fixed and resubmitted. A second submission gets 409 `duplicate`. A stale or
  missing decision id gets 409. Completion comes from the desk, never the agent's final text.
- Outcome order: accepted, then worker unreachable (`tool_error`), then timed out, then only
  invalid attempts (`invalid_submission`), then non-zero exit (`tool_error`), else
  `no_submission`.
- Each decision is a fresh Docker Agent session with a new HOME; nothing the agent wrote
  carries to the next decision, and account state comes only from `get_portfolio`. Episode
  memory, if wanted later, may only hold what earlier decisions could see.
- Tools are rendered as `api` toolsets from provider-defined `HttpTool`s, not `openapi`
  toolsets (the 2026-10-28 notes proposed OpenAPI tools). The `api` syntax is documented
  end to end. How `openapi` toolsets resolve the base URL of a spec without `servers` is
  not; switch once that is verified.
- `harness.production.proposal_from_production` maps portfolio-builder submissions
  (`company_id` holdings, implied exits) to stock-code proposals.
- portfolio-builder (#475): `PortfolioSubmitter` protocol with `DatabaseSubmitter` (existing
  `save_portfolio`/`save_trace`) and `EvaluationSubmitter`, which writes `submission.json`
  and `trace.json` and never touches the database. A registry is selected by
  `PORTFOLIO_BUILDER_SUBMITTER=database|evaluation` (default `database`), following the
  service prefix convention. Evaluation mode skips the unexplained-retry read and the
  explanation step.

## Sequencing (#439)

- Schedules: `{"every": "day"|"week", "start", "end", "at", "weekday"}` produce weekday KST
  decision times. `weekly_single_shot` expands to one episode per week. Each episode starts
  from initial capital; state carries between decisions inside an episode.
- The same loop runs single-shot and sequential episodes. A later decision before a fill
  cancels the pending orders (recorded). A holiday or after-close decision fills at the next
  trading open. Buys are cut to affordable cash after sells.
- A decision with no fill date before the episode end fails the episode explicitly.
- Episode end on a holiday: the harness loads up to 14 days past the end to tell a holiday
  from missing data. It never uses those bars for fills or marks.

## Records and metrics (#438)

- `Experiment → Run → Episode → Decision → Execution → PortfolioSnapshot → EvaluationResult`
  maps to `run.json` (spec, manifests, versions, rules, status), `episodes/<id>.json` (decision
  records with audit, fills and rejections tagged with `decided_at`, cancellations,
  snapshots, failure), and `evaluation.json`.
- Status: `completed`, `partial` (some decisions failed), `no_valid_decision`, `failed`.
  Metrics are withheld for the last two; failures are never scored.
- Return, max drawdown and Sortino come from initial capital plus marked closes. Sortino
  uses the downside RMS against the per-period target and the mean excess over the
  per-period risk-free rate, annualized by `periods_per_year`. It is undefined, with a
  reason, for fewer than two returns or zero downside. Turnover is traded value over mean
  value; fees and taxes are reported separately. Baselines are Equal-Weight (rebalanced at
  the same decision times) and Buy-and-Hold.
- The `(Final Return − MDD + Sortino) / 3` composite from the 2026-10-28 notes is not
  computed. It remains a candidate there, and #438 rules out a single averaged score;
  indicators are reported separately.
- Replay re-simulates stored decisions (failures and holds included) without the model and
  reports differing record fields. `harness evaluate` recomputes metrics from records.
  Reproducibility lists missing commit, image ids, dataset version, information version or
  decision version.

## Compose (#437)

- `compose.evaluate.yaml` (runner and `market-http-prices`) plus layered overrides in
  `evaluations/compose/`: `information.{none,market,news,features}.yaml` and
  `decision.{rule,docker-agent}.yaml`. Overrides are used instead of profiles so that an
  unselected service and its `depends_on` never exist. Tests render every combination.
- `scripts/run-evaluation.sh SPEC INFORMATION DECISION` uses a unique project per run,
  records the commit (`-dirty` when modified) and image ids, and runs
  `up --abort-on-container-exit --exit-code-from runner`. It removes the run's volumes
  afterwards.
- Networks: `dataset` (external, internal) for datastores and data servers; `harness` for
  prices; `information` for PIT and feature servers; `decision` for the desk and worker;
  `egress` only for the worker.

## Relation to the 2026-11-01 harness research

The research note (Harbor, TraderHarness, TradeBench) is a proposal and leaves Harbor
adoption, single- versus multi-step, and the entry point open. This implementation keeps
the Compose-first runner from #433 and #437 and satisfies the note's invariants:

- Agents and the price path never share access to future prices.
- Agents submit target portfolios and never change fills or account state.
- Agent workspaces mount no source data.
- The same data, rules and stored decisions replay to the same result.

The pieces map onto Harbor's split if it is adopted later: a run is a Job of Trials
(episodes), the runner and simulator are the environment, and the evaluator is the
verifier. Date and ticker masking against pretraining memory is not implemented.
