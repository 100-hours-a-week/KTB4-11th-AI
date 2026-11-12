# Evaluation harness

`harness` runs a fixed investment loop (Advance → Observe → Decide → Submit) for an injected
information variant and decision system. It simulates fills on historical daily bars, then
writes versioned records that can be replayed and re-scored. Design:
[`2026-11-03-evaluation-harness-design.md`](../../docs/superpowers/specs/2026-11-03-evaluation-harness-design.md).

There are two ways to start it:

| Way | Use it for | Needs |
| --- | --- | --- |
| [Local](#start-locally) | Developing, checking a spec, rule-based decision systems | `uv`, a QuestDB with daily bars |
| [Compose](#start-with-compose) | Comparable experiments, Docker Agent, all information variants | Docker, a dataset snapshot |

Local runs skip the network isolation, so use Compose for results you compare.

## Start locally

Run every command from the repository root.

1. Install the workspace and make sure QuestDB holds daily bars for the experiment's
   universe and dates, for example from market-collector or a restored snapshot.

   ```bash
   uv sync --all-packages --locked --group migrations
   docker compose -f compose.local.yaml up -d questdb
   ```

2. Start the harness-only price server. It has no cutoff and only the harness may call it.

   ```bash
   MARKET_HTTP_QUESTDB_CONF='ws::addr=localhost:9000;connect_timeout=5000;' \
   MARKET_HTTP_EVALUATION_ACCESS=harness \
   MARKET_HTTP_PORT=8101 \
   uv run market-http-evaluation
   ```

3. In another terminal, run an experiment with Equal-Weight and no information:

   ```bash
   mkdir -p evaluations/results/clock
   HARNESS_SPEC_FILE=evaluations/experiments/weekly-single-shot-2026h2.json \
   HARNESS_PRICES_URL=http://127.0.0.1:8101 \
   HARNESS_CLOCK_FILE=evaluations/results/clock/as_of \
   HARNESS_RESULTS_DIR=evaluations/results \
   uv run harness run; echo "exit $?"
   ```

   Records land in `evaluations/results/<experiment_id>/<run_id>/` (git-ignored). Set
   `HARNESS_DECISION_SYSTEM=buy-and-hold` to try another baseline.

To give a decision system point-in-time data, start the point-in-time (PIT) market server
on the same clock file. The runner writes the clock before each decision, and the server
reads it on every request:

```bash
MARKET_HTTP_QUESTDB_CONF='ws::addr=localhost:9000;connect_timeout=5000;' \
MARKET_HTTP_EVALUATION_ACCESS=pit \
MARKET_HTTP_EVALUATION_CLOCK_FILE=evaluations/results/clock/as_of \
MARKET_HTTP_EVALUATION_PUBLIC_URL=http://127.0.0.1:8102 \
MARKET_HTTP_PORT=8102 \
uv run market-http-evaluation
```

```bash
HARNESS_SPEC_FILE=evaluations/experiments/weekly-single-shot-2026h2.json \
HARNESS_PRICES_URL=http://127.0.0.1:8101 \
HARNESS_CLOCK_FILE=evaluations/results/clock/as_of \
HARNESS_RESULTS_DIR=evaluations/results \
HARNESS_INFORMATION_VARIANT=market \
HARNESS_MARKET_URL=http://127.0.0.1:8102 \
HARNESS_DECISION_SYSTEM=jev-stub \
uv run harness run
```

`news-http-evaluation`, `disclosure-http-evaluation` and `harness-features` start the same
way. Each needs the same clock file and its own `*_PUBLIC_URL`; see
[Settings](#settings). Every server a decision system calls must publish an OpenAPI
`servers` entry. Without one, the run stops at start with `<spec> has no servers entry`.

## Start with Compose

Each run is its own Compose project, so containers, networks, the clock, and agent
sessions never mix between runs, and runs may go in parallel. Decision containers cannot
reach the datastores or the harness-only prices.

1. Set the dataset credentials in `.env`, then create the dataset network and start the
   shared source data once:

   ```bash
   docker network create --internal ktb4-evaluation-dataset
   docker compose -f evaluations/compose/dataset.yaml up -d
   ```

   Load a snapshot into it through the loopback admin ports, `127.0.0.1:19000` for QuestDB
   and `127.0.0.1:15432` for PostgreSQL (migrated to head). Put the snapshot's name in the
   spec's `dataset_version`.

2. Set what the run needs in `.env`. The full list is in
   [`.env.example`](../../.env.example) under *evaluation harness*.

   | Needed by | Variables |
   | --- | --- |
   | `raw`, `enriched`, `disclosures` | `EVALUATION_POSTGRES_DSN` (for example `postgresql+psycopg://ktb:ktb@postgres:5432/ktb`) |
   | `disclosures` | `DISCLOSURE_INFERENCE_BACKEND` (default `modal`), `DISCLOSURE_MODAL_KEY`, `DISCLOSURE_MODAL_SECRET`, `DISCLOSURE_MODAL_QUERY_URL`, `DISCLOSURE_MODAL_RERANK_URL` |
   | `docker-agent` | `DOCKER_AGENT_URL` and `DOCKER_AGENT_SHA256` (release asset and its checksum), `EVALUATION_MODEL_PROVIDER`, `EVALUATION_MODEL`, `EVALUATION_MODEL_API_KEY_ENV` (such as `OPENROUTER_API_KEY`), `EVALUATION_MODEL_API_KEY_FILE` (a file holding the key) |

   The model key goes to the agent as a Compose secret file and never sits in a container
   environment, the agent config, or the records. Compose mounts the file with its host
   owner, so the agent worker runs as the user who starts the script. Keep the key file
   readable by that user; mode `0600` works. The worker reads the key once at start, and an
   unreadable or empty file stops it before any decision.

3. Run one information variant with one decision system:

   ```bash
   scripts/run-evaluation.sh evaluations/experiments/weekly-single-shot-2026h2.json raw equal-weight
   scripts/run-evaluation.sh evaluations/experiments/daily-rebalancing-2026q3.json enriched docker-agent
   ```

   The script builds the images, records the commit (with `-dirty` for uncommitted
   changes) and image ids, and runs the project until the runner exits. It returns the
   runner's exit code and removes the run's containers and volumes. Records go to
   `evaluations/results/` unless `EVALUATION_RESULTS_DIR` says otherwise.

| Information | Adds | Agent tools |
| --- | --- | --- |
| `none` | nothing | none |
| `market` | `market-http-pit` | `get_daily_bars` |
| `raw` | + `news-http-pit` | + `search_news` |
| `enriched` | `raw` + `features` | + `get_technicals` |
| `disclosures` | `raw` + `disclosure-http-pit` | + `list_disclosures`, `search_disclosures`, `get_disclosure_sections`, `get_disclosure_financials` |

| Decision | Runs in | Notes |
| --- | --- | --- |
| `equal-weight` | runner | equal weights at every decision |
| `buy-and-hold` | runner | equal weights once, then holds |
| `jev-stub` | runner | Jev-shaped adapter with a momentum stub; needs `market.daily_bars` |
| `docker-agent` | `agent-worker` | Docker Agent with the variant's tools plus `get_portfolio` and `submit_portfolio` |

A decision system that needs a capability the variant lacks exits with code 4. Nothing
falls back silently.

## Experiment spec

A spec is JSON; see [`evaluations/experiments/`](../experiments/).

```json
{
  "experiment_id": "weekly-single-shot-2026h2",
  "dataset_version": "questdb-postgres-snapshot-20261103",
  "universe": ["005930", "000660"],
  "initial_capital": 100000000,
  "fee_rate": 0.00015,
  "sell_tax_rate": 0.002,
  "metrics": {"periods_per_year": 252, "target_return": 0.0, "risk_free_rate": 0.0},
  "weekly_single_shot": {"first_week": "2026-07-06", "last_week": "2026-10-26", "at": "08:00"}
}
```

- `weekly_single_shot` makes one episode per week: a decision on `weekday` (0 = Monday) at
  `at` KST, ending `end_offset_days` later at `end_at`.
- `episodes` lists episodes explicitly. Each one has an `id`, an `end`, and either
  `decision_times` (timezone-aware) or a `schedule` such as
  `{"every": "day", "start": "2026-07-01", "end": "2026-09-29", "at": "16:00"}`. Schedules
  produce weekdays only, and `"every": "week"` also takes a `weekday`.
- Every episode starts from `initial_capital`. Holdings carry between decisions within an
  episode. Fills happen at the open of the first trading day after the decision, and that
  day's close must be at or before the episode end so the fill is valued. Otherwise the
  episode fails (`no fill date whose close is before episode end`), so end episodes after
  15:30 KST.

## Results

| Exit | Meaning |
| --- | --- |
| 0 | every episode completed |
| 2 | every episode finished, but some decisions failed (no submission, invalid submission, timeout, tool error) |
| 3 | an episode failed on missing data or the calendar |
| 4 | the decision system needs a capability the information variant lacks |
| 1 | configuration or unexpected error, such as a spec without a `servers` entry |

| File | Content |
| --- | --- |
| `run.json` | spec, information and decision manifests (OpenAPI specs, servers, operations), versions, execution and metric rules, status, reproducibility |
| `episodes/<id>.json` | decisions with their audit (prompt, agent output, submission attempts, model choices), fills, rejections, cancellations, snapshots, failure |
| `evaluation.json` | status, Return, max drawdown, Sortino, turnover, costs and activity per episode, with Equal-Weight and Buy-and-Hold baselines and a summary |

Failed decisions and episodes are recorded, never scored: `no_valid_decision` and `failed`
episodes have no metrics.

```bash
uv run harness evaluate RUN_DIR                    # recompute metrics from the records
uv run harness export RUN_DIR fills > fills.csv    # decisions | fills | rejections | snapshots
uv run harness replay RUN_DIR --prices-url URL     # re-simulate stored decisions; exit 1 on differences
```

`replay` never calls a model. Point `--prices-url` at a harness-access
`market-http-evaluation` on the same dataset. For a Compose dataset, that means
`MARKET_HTTP_QUESTDB_CONF='ws::addr=localhost:19000;'`. A model rerun is just another run of
the same spec.

## Settings

| Prefix | Service | Required | Common optional |
| --- | --- | --- | --- |
| `HARNESS_` | `harness run` | `SPEC_FILE`, `PRICES_URL`, `CLOCK_FILE` | `RESULTS_DIR` (`results`), `RUN_ID`, `INFORMATION_VARIANT` (`none`), `MARKET_URL`, `NEWS_URL`, `FEATURES_URL`, `DISCLOSURES_URL`, `DECISION_SYSTEM` (`equal-weight`), `DECISION_TIMEOUT_SECONDS` (900), `CODE_COMMIT`, `IMAGE_DIGEST` |
| `HARNESS_` | runner with `docker-agent` | also `AGENT_WORKER_URL`, `SUBMISSION_URL` | `SUBMISSION_PORT` (8100) |
| `HARNESS_AGENT_` | `harness-agent-worker` | `MODEL_PROVIDER`, `MODEL` | `API_KEY_FILE` with `API_KEY_ENV`, `MODEL_BASE_URL`, `MAX_TOKENS`, `INSTRUCTION_FILE`, `BINARY`, `RUN_ARGS` (`run --exec --yolo`), `PORT` (8200) |
| `HARNESS_FEATURES_` | `harness-features` | `MARKET_URL` (the PIT market server), `PUBLIC_URL` | `PORT` (8000) |
| `MARKET_HTTP_` | `market-http-evaluation` | `QUESTDB_CONF`, `EVALUATION_ACCESS` (`pit` or `harness`); with `pit`, also `EVALUATION_CLOCK_FILE` and `EVALUATION_PUBLIC_URL` | `PORT` (8000) |
| `NEWS_HTTP_` | `news-http-evaluation` | `POSTGRES_DSN`, `EVALUATION_CLOCK_FILE`, `EVALUATION_PUBLIC_URL` | `EVALUATION_AVAILABILITY` (`published` or `fetched`), `PORT` |
| `DISCLOSURE_HTTP_` | `disclosure-http-evaluation` | `POSTGRES_DSN`, `EVALUATION_CLOCK_FILE`, `PUBLIC_URL`, plus `DISCLOSURE_*` model settings | `PORT` |

## What decision systems can see

- The harness's own prices are never passed to a decision system.
- Daily bars appear after their 15:30 KST close.
- News appears once `published_at` is at or before the clock. Cluster summaries and graphs
  are not served, because they are rebuilt from later articles.
- Filings appear when published before the clock's KST date, so a same-day filing waits for
  the next day.
- Request parameters cannot move the cutoff, and an unreadable clock returns 503.

## Known limitations

- Historical index membership is not stored, so the universe is a list chosen today and
  carries survivorship bias.
- Bars are adjusted prices stored at collection time, so episodes are assumed free of
  corporate actions.
- News corrections overwrite earlier text, and backfilled articles have a late
  `fetched_at`.
- Disclosure vectors are built after the filing dates, so reindexing with a new model
  changes past search results.
- Models may have learned post-cutoff events in pretraining. Record their published cutoffs.
- Each Docker Agent decision starts a fresh session, so the agent's notes don't carry to
  the next decision.
- portfolio-builder in `evaluation` submitter mode still reads its briefing from the
  production database. It doesn't write to it.

## Tests

```bash
uv run pytest evaluations
```

The Compose tests render every combination with `docker compose config`; they skip when
the Docker CLI is missing.
