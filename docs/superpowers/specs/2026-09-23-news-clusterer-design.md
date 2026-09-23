# news-clusterer — Incremental DBSCAN + LLM Summaries

**Date:** 2026-09-23
**Status:** Approved for planning

## 1. Purpose and scope

Turn the `news-clusterer` skeleton into a cron job that groups every embedded article into
topics/events and stores each cluster in PostgreSQL with its member articles, a
representative title and a summary. `portfolio-builder` reads the clusters from
PostgreSQL.

### Non-goals

- Any HTTP surface. The FastAPI app and `GET /health` are removed.
- Expiring old articles from clustering (see §8).
- Per-cluster sentiment, tickers or scores.
- Deployment mechanics (ECS task, schedule, volume provisioning).

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Trigger | Cron: `main()` runs once and exits | Clustering is batch work; nothing needs to call it synchronously |
| Consumer contract | `portfolio-builder` reads `clusters` / `article_clusters` from PostgreSQL | Removes the only service-to-service HTTP edge; every service now talks through datastores |
| Algorithm | Incremental DBSCAN (insert-only, Ester et al. 1998), own code on numpy | Each run costs O(new × total) instead of reclustering everything; cluster ids stay stable across runs |
| State between runs | Pickle checkpoint at `/app/news-clusterer/checkpoint/dbscan_checkpoint.pkl` on a Docker volume | DBSCAN needs every point's neighbour count and label; recomputing them from PostgreSQL each run would be a full recluster |
| Cluster ids | Assigned by the checkpoint, not by PostgreSQL | Merges and new clusters are decided in memory before any write |
| Lost or mismatched checkpoint | Full rebuild: clear the cluster tables, treat every embedded article as new | Ids come from the checkpoint, so without it the stored ids have no owner |
| New-article detection | Embedded article ids minus checkpoint ids | news-preprocessor embeds after inserting, so an older id can be embedded late; a max-id cursor would skip it |
| Distance | Cosine distance `1 - x·y` on the stored unit vectors | Embeddings are already L2-normalised (news-scraper spec) |
| Summaries | vLLM OpenAI-compatible `POST /chat/completions`, full article bodies up to a character budget, JSON `{title, summary}` in Korean | Articles are Korean; full bodies give better summaries |
| Summary trigger | Only clusters whose membership changed | LLM cost scales with change, not with total clusters |
| LLM endpoint | Required setting, no default | Keeps team infrastructure addresses out of the repo |

## 3. Run flow

1. **Load checkpoint.** If the file is missing, or its `eps` / `min_samples` differ from
   the current settings, start from an empty checkpoint and mark the run as a rebuild.
2. **Find new articles.** `SELECT id FROM articles WHERE embedding IS NOT NULL`, subtract
   the checkpoint's ids, then fetch vectors for the remainder ordered by `id`.
3. **Insert** each new article into the in-memory state in `id` order (§4). Collect the
   set of cluster ids that gained members, the set absorbed by merges, and every article
   whose label changed.
4. **Write clusters** in one transaction (§5). On a rebuild, the transaction first
   truncates both cluster tables.
5. **Summarize** every cluster that needs it (§6), one transaction per cluster.
6. **Save checkpoint** to a temp file in the same directory, then `os.replace` it over
   the old one.
7. Exit 1 if any summary failed, else 0.

A crash between steps 4 and 6 leaves PostgreSQL ahead of the checkpoint. The next run
replays the same insertions from the old checkpoint; insertion order is deterministic and
step 4 only upserts, so it reproduces the same rows.

A summary failure leaves the cluster flagged (§5), so the next run retries it even if the
cluster did not change again.

## 4. Incremental DBSCAN

State (all arrays row-aligned, stored in the checkpoint):

| Field | Type | Meaning |
|---|---|---|
| `article_ids` | `int64[n]` | `articles.id` of each point |
| `vectors` | `float32[n, 2000]` | Unit-length embeddings |
| `counts` | `int32[n]` | Size of the eps-neighbourhood, including the point itself |
| `labels` | `int64[n]` | Cluster id, or `-1` for noise |
| `next_cluster_id` | `int` | Next id to hand out, starting at 1 |
| `eps`, `min_samples` | `float`, `int` | Parameters the state was built with |

A point is **core** when `counts >= min_samples`. Neighbours of `v` are points with
`1 - vectors @ v <= eps`.

Inserting point `p`:

1. `N` = existing neighbours of `p`. Append `p` with `counts = |N| + 1`, `label = -1`.
   Increment `counts` for every point in `N`.
2. `new_cores` = points in `N ∪ {p}` that are core now but were not before this insert.
3. If `new_cores` is empty: if `N` holds a core point, `p` takes the label of its most
   similar core neighbour (border point); otherwise `p` stays noise. Done.
4. Otherwise let `R` = the union of the neighbourhoods of all `new_cores`. `L` = labels of
   the points in `R` that were core **before** this insert (a new core's old label is
   only a border label and does not force a merge).
   - `L` empty → `target = next_cluster_id`, then increment it.
   - otherwise `target = min(L)`; every point labelled with another id in `L` is
     relabelled to `target`, and those ids are recorded as merged.
5. Label every point in `new_cores` with `target`, and every noise point in `R` with `target`.

Only a new core can create or connect clusters in an insert-only stream, so this keeps
the same core points and core-to-core connectivity as batch DBSCAN over the same points.
Border points adjacent to two clusters may land in either, as in batch DBSCAN.

## 5. Schema (migration `0002`)

```
clusters
  id             bigint PRIMARY KEY            -- from the checkpoint
  title          text NULL
  summary        text NULL
  updated_at     timestamptz NOT NULL DEFAULT now()
  summarized_at  timestamptz NULL

article_clusters
  article_id  bigint PRIMARY KEY REFERENCES articles(id)
  cluster_id  bigint NOT NULL REFERENCES clusters(id) ON DELETE CASCADE
  INDEX (cluster_id)
```

A cluster **needs a summary** when `summarized_at IS NULL OR summarized_at < updated_at`.
Noise articles have no `article_clusters` row.

Write order inside the step-4 transaction:

1. Upsert every cluster that gained members with `updated_at = now()`.
2. Upsert `article_clusters` for every article whose label changed to a cluster id.
3. Delete the merged cluster ids. Their members were re-pointed in step 2, so the cascade
   removes nothing that is still needed.

## 6. Summaries

For each cluster that needs a summary:

- Load member articles ordered by `published_at DESC`. Concatenate `title\n\nbody`
  blocks, stopping before the total exceeds `summary_max_chars`. At least the newest
  article is always included, truncated to the budget.
- `POST {llm_base_uri}/chat/completions` with `llm_model`, a Korean system prompt asking
  for one event title and a short summary, and a JSON response format. The exact
  `response_format` shape vLLM accepts is verified during planning.
- Parse `{"title": str, "summary": str}`. On success, set `title`, `summary` and
  `summarized_at = now()`. On any HTTP, timeout or parse error, log it, leave the cluster
  flagged and continue with the next one.

## 7. Code, configuration and packaging

### Modules (`services/news-clusterer/src/news_clusterer/`)

| Module | Top-level function |
|---|---|
| `settings.py` | `Settings` |
| `checkpoint.py` | `Checkpoint` state, `load_checkpoint()`, `save_checkpoint()` |
| `dbscan.py` | `insert()` — §4 on one point |
| `storage.py` | SQL for §3 step 2, §5 writes and §6 reads/updates |
| `summarize.py` | `summarize()` — one cluster to `{title, summary}` via httpx |
| `__main__.py` | `main()` — the §3 flow |

`app.py` and `tests/test_app.py` are deleted.

### Settings (`NEWS_CLUSTERER_` prefix)

| Setting | Default |
|---|---|
| `postgres_dsn` | required |
| `llm_base_uri` | required (includes `/v1`) |
| `llm_model` | required |
| `eps` | `0.2` (cosine distance; tune on real data) |
| `min_samples` | `3` (tune on real data) |
| `checkpoint_path` | `/app/news-clusterer/checkpoint/dbscan_checkpoint.pkl` |
| `summary_max_chars` | `24000` |
| `llm_timeout` | `120` seconds |
| `log_level` | `INFO` |

`host` and `port` are removed.

### Dependencies

Remove `fastapi` and `uvicorn`. Add `numpy`, `httpx`, `sqlalchemy`, `psycopg[binary]` and
`pgvector`, matching news-preprocessor's versions. Then `uv lock` and regenerate
`docker/requirements/news-clusterer.txt` as described in `AGENTS.md`.

### Docker and compose

- `docker/news-clusterer.Dockerfile`: drop `EXPOSE 8000`; create
  `/app/news-clusterer/checkpoint` owned by `app` (uid 10001) before `USER app`, so a
  fresh named volume mounted there inherits writable ownership.
- `compose.dev.yaml`: add a `news-clusterer` service under the `jobs` profile, depending
  on a healthy `postgres`, with a named volume `news-clusterer-checkpoint` mounted at
  `/app/news-clusterer/checkpoint`, `restart: "no"`, and the `NEWS_CLUSTERER_*`
  variables passed through from the environment.
- CI builds the image only; no workflow change is needed.

### Documentation

- `AGENTS.md`: the clusterer row becomes a cron service; `portfolio-builder` depends on
  `ktb-core` and `ktb-market-analyzer` only; the "Services communicate through datastores"
  bullet drops the HTTP exception.
- The monorepo design spec keeps its history; this spec supersedes its §2 "one
  synchronous edge".

## 8. Limits and risks

- **Memory and time grow with total articles.** Vectors take about 8 KB per article
  (about 800 MB at 100,000 articles), and each insert is a dot product against every
  point. Upgrade path: expire articles older than a window out of the checkpoint.
- **Pickle executes code on load.** Anyone who can write the checkpoint volume can run
  code as the job. The volume must be private to this job.
- **Default `eps` / `min_samples` are guesses.** They need tuning against real embeddings;
  changing either triggers a full rebuild (§3 step 1).

## 9. Testing

- `dbscan`: insert a synthetic set of unit vectors one by one and compare with a
  brute-force batch DBSCAN written in the test: same core points, same partition of core
  points, same noise set, and every border point labelled with a cluster of one of its
  core neighbours. Include a case where a bridging point merges two clusters and the
  lower id survives.
- `checkpoint`: save/load round trip; missing file and changed `eps` both produce an
  empty state marked as a rebuild.
- `storage` (PostgreSQL fixtures, skipped without `KTB_TEST_POSTGRES_DSN`): new-article
  detection including a late-embedded older id; the §5 write order with a merge; rebuild
  truncation; the needs-summary query.
- `summarize`: `httpx.MockTransport` for a valid reply, malformed JSON and an HTTP error;
  the character budget keeps the newest article.
- `main`: end to end against PostgreSQL with a mocked LLM; a second run with no new
  articles changes nothing and makes no LLM call.
