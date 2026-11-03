# Evaluation harness

`evaluations/harness` (module `harness`) runs a fixed investment loop against an injected
information architecture and decision system, simulates execution on historical daily bars,
and writes versioned records that can be replayed. Design:
[`2026-11-03-evaluation-harness-design.md`](../docs/superpowers/specs/2026-11-03-evaluation-harness-design.md).

## Run an experiment

1. Create the dataset network and start the shared, read-only source data once. Restore the
   snapshot into its volumes and name it in the spec's `dataset_version`.

   ```bash
   docker network create --internal ktb4-evaluation-dataset
   docker compose -f evaluations/compose/dataset.yaml up -d
   ```

2. Run one combination. Each run is its own Compose project, so containers, networks, the
   clock, and agent homes never mix between runs; runs may go in parallel.

   ```bash
   scripts/run-evaluation.sh evaluations/experiments/weekly-single-shot-2026h2.json raw equal-weight
   scripts/run-evaluation.sh evaluations/experiments/weekly-single-shot-2026h2.json enriched docker-agent
   ```

   | Information | Services added | Tools offered to decision systems |
   | --- | --- | --- |
   | `none` | none | none |
   | `market` | `market-http-pit` | `get_daily_bars` |
   | `raw` | + `news-http-pit` | + `search_news` (raw articles) |
   | `enriched` | + `features` | + `get_technicals` |

   | Decision | Where it runs | Needs |
   | --- | --- | --- |
   | `equal-weight`, `buy-and-hold` | runner | nothing |
   | `jev-stub` | runner | `market.daily_bars` (an explicit `unsupported` exit otherwise) |
   | `docker-agent` | `agent-worker` | `DOCKER_AGENT_URL`/`DOCKER_AGENT_SHA256`, `EVALUATION_MODEL_*`, a key file |

   The Docker Agent key is passed as a Compose secret file (`EVALUATION_MODEL_API_KEY_FILE`)
   and handed only to the `docker-agent` process; it is never in the environment of other
   containers, the agent config, or the records.

3. Exit codes come from the runner: `0` every episode completed, `2` some decisions failed
   (no submission, invalid submission, timeout, tool error) but every episode finished, `3` an
   episode failed on data or calendar, `4` the decision system needs a capability the
   information variant lacks, `1` an unexpected error.

## Boundaries

- Decision systems never get the harness `PriceSource`. Execution and marking read
  `market-http-prices`, which sits on the `harness` network only.
- Point-in-time data comes from `market-http-evaluation` (`MARKET_HTTP_EVALUATION_ACCESS=pit`)
  and `news-http-evaluation`. Both read the cutoff from the clock file the runner writes before
  each decision; request parameters cannot move it, and an unreadable clock returns 503.
- Daily bars are visible after their 15:30 KST close. News is visible once `published_at` is
  at or before the clock (`fetched` availability also requires `fetched_at`). Cluster
  summaries and graphs are not served: they are rebuilt from later articles.
- `features` computes technicals only from `market-http-pit`, so it inherits the cutoff.
- The agent worker joins `information`, `decision` (the runner's submission desk) and
  `egress` (the model API). It cannot reach `dataset` or `harness`.

## Records

`evaluations/results/<experiment_id>/<run_id>/`:

| File | Content |
| --- | --- |
| `run.json` | spec, information and decision manifests, versions (commit, image ids, dataset), execution and metric rules, status, exit code, reproducibility |
| `episodes/<id>.json` | decisions with audit (prompt, tool list, agent output, submission attempts, model choices), cancellations, fills, rejections, snapshots, calendar gaps, final state, failure |
| `evaluation.json` | per-episode status, metrics, activity, Equal-Weight and Buy-and-Hold baselines, comparison, summary, metric definitions |

```bash
uv run harness evaluate RUN_DIR                       # recompute metrics from snapshots
uv run harness replay RUN_DIR --prices-url URL        # re-simulate stored decisions, no model calls
uv run harness export RUN_DIR fills > fills.csv       # decisions | fills | rejections | snapshots
```

A model rerun is a new run of the same spec. `reproducibility.status` is `incomplete` when the
commit, image ids, dataset version, information version, or decision version is missing.

## Known limitations

- Historical index membership is not stored; the universe is an explicit list chosen today,
  which carries survivorship bias.
- Bars are adjusted prices stored at collection time; episodes are assumed free of corporate
  actions.
- News corrections overwrite earlier text, and backfilled articles have late `fetched_at`.
- Models may have learned post-cutoff events in pretraining; record their published cutoffs.
- portfolio-builder in `evaluation` submitter mode still reads its briefing and previous
  portfolio from the production database; it does not write to it.
