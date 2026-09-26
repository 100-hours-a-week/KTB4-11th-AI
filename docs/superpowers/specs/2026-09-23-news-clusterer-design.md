# news-clusterer — DBSCAN + LLM Summaries

**Date:** 2026-09-23 (revised 2026-09-24)
**Status:** Approved for planning

## 1. Purpose and scope

Turn the `news-clusterer` skeleton into a cron job that groups every embedded article into
topics/events and stores each cluster in PostgreSQL with its member articles, a
representative title and a summary. `portfolio-builder` reads the clusters from
PostgreSQL.

### Non-goals

- Any HTTP surface. The FastAPI app and `GET /health` are removed.
- Incremental DBSCAN or online learning. Every run clusters all articles from scratch;
  an incremental algorithm replaces it only once a full run no longer fits (§8).
- A checkpoint or any state outside PostgreSQL.
- Per-cluster sentiment, tickers or scores.
- Deployment mechanics (ECS task, schedule).

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Trigger | Cron: `main()` runs once and exits | Clustering is batch work; nothing needs to call it synchronously |
| Consumer contract | `portfolio-builder` reads `clusters` / `article_clusters` from PostgreSQL | Removes the only service-to-service HTTP edge; every service now talks through datastores |
| Algorithm | Batch DBSCAN over all embedded articles every run | Simplest correct model; revisit when a run gets too slow or too large (§8) |
| Implementation | Our own DBSCAN on numpy; scikit-learn only in tests as the reference | Keeps sklearn and scipy out of the image; sklearn proves correctness (§9) |
| Distance | Cosine distance `1 - x·y` on the stored unit vectors | Embeddings are already L2-normalised (news-scraper spec) |
| Previous state | Read from `article_clusters` | PostgreSQL already holds last run's assignment; no checkpoint file or volume |
| Cluster identity | Match each new cluster to the old cluster it shares the most articles with (§5) | Stable ids for `portfolio-builder`; unchanged clusters keep their summary |
| Summaries | vLLM OpenAI-compatible `POST /chat/completions`, full article bodies up to a character budget, JSON `{title, summary}` in Korean | Articles are Korean; full bodies give better summaries |
| Summary trigger | Only clusters whose membership changed | LLM cost scales with change, not with total clusters |
| LLM endpoint | Required setting, no default | Keeps team infrastructure addresses out of the repo |

## 3. Run flow

1. **Load** every embedded article: `SELECT id, embedding FROM articles WHERE embedding
   IS NOT NULL ORDER BY id`.
2. **Cluster** them with `dbscan()` (§4), then log the cost of steps 1–2 (§3.1).
3. **Load the previous assignment** from `article_clusters`.
4. **Match** new clusters to old ones and **write** the result in one transaction (§5).
5. **Summarize** every cluster that needs it (§6), one transaction per cluster.
6. Exit 1 if any summary failed, else 0.

With no new articles, the same input produces the same clusters, every cluster matches
its old self unchanged, and the run writes nothing and calls no LLM.

A summary failure leaves the cluster flagged (§5), so the next run retries it even if the
cluster did not change again.

### 3.1 Clustering cost log

After step 2, `main()` logs one INFO line so the §8 decision rests on measured numbers:

```
clustering cost: articles=12345 clusters=210 noise=4021 load_seconds=3.21 dbscan_seconds=58.40 peak_rss_mib=1532
```

- `load_seconds` / `dbscan_seconds`: `time.perf_counter()` around step 1 and step 2.
- `peak_rss_mib`: `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024` (Linux
  reports KiB). It is the process peak so far, which after step 2 is the loaded vectors
  plus DBSCAN's working memory; steps 3–5 are much smaller.
- The values go in the message text because `ktb_core.logging.JsonFormatter` does not
  emit `extra` fields. Keep the `key=value` format stable so log queries can parse it.

## 4. DBSCAN

`dbscan(vectors: float32[n, d], eps: float, min_samples: int) -> int64[n]` returns a
label per row, `-1` for noise, clusters numbered `0..k-1`. Semantics match
`sklearn.cluster.DBSCAN(eps=eps, min_samples=min_samples, metric="cosine")`:

- Neighbours of point `i` are the points `j` (including `i` itself) with
  `1 - vectors[i] @ vectors[j] <= eps`.
- `i` is **core** when it has at least `min_samples` neighbours.
- Visit points in index order. Each unlabelled core point starts a new cluster, which is
  expanded through core neighbours; non-core neighbours reached by the expansion join the
  cluster as border points if they are still unlabelled.
- Points never reached stay `-1`.

Neighbour lists are computed in row blocks (`vectors[block] @ vectors.T`, block size
fixed in code) so peak memory is `block × n` floats rather than `n × n`.

## 5. Schema (migration `0002`) and matching

```
clusters
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
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

**Matching** (`match()`, pure Python, no I/O). Input: new clusters and old clusters, each
as a set of article ids. Output: for each new cluster an old id or none, plus the old ids
left unmatched.

1. For every (new, old) pair with at least one shared article, compute the overlap size.
2. Take pairs in descending overlap order, ties broken by lower old id then lower new
   label; accept a pair when neither side is matched yet.
3. Unmatched new clusters get no old id; unmatched old ids are returned for deletion.

**Write** in one transaction:

1. Insert a `clusters` row for every unmatched new cluster (`summarized_at` NULL).
2. For every matched new cluster whose article set differs from its old one, set
   `updated_at = now()`.
3. Replace `article_clusters` rows for articles whose cluster changed: delete rows for
   articles that became noise, upsert the rest.
4. Delete the unmatched old clusters. Their remaining members were re-pointed in step 3
   or became noise, so the cascade removes nothing that is still needed.

## 6. Summaries

> **Superseded (2026-09-25):** summaries moved to news-graph-builder and to the
> `cluster_summaries` table; see `2026-09-24-news-graph-builder-design.md`.

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
| `dbscan.py` | `dbscan()` — §4 |
| `match.py` | `match()` — §5 matching |
| `storage.py` | SQL for §3 steps 1 and 3, §5 writes and §6 reads/updates |
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
| `summary_max_chars` | `24000` |
| `llm_timeout` | `120` seconds |
| `log_level` | `INFO` |

`host` and `port` are removed.

### Dependencies

- `news-clusterer`: remove `fastapi` and `uvicorn`; add `numpy`, `httpx`, `sqlalchemy`,
  `psycopg[binary]` and `pgvector`, matching news-preprocessor's versions.
- Root `dev` group: add `scikit-learn`. It is a test-only reference and must not reach
  the image.
- Then `uv lock` and regenerate `docker/requirements/news-clusterer.txt` as described in
  `AGENTS.md`.

### Docker and compose

- `docker/news-clusterer.Dockerfile`: drop `EXPOSE 8000`.
- `compose.dev.yaml`: add a `news-clusterer` service under the `jobs` profile, depending
  on a healthy `postgres`, `restart: "no"`, with the `NEWS_CLUSTERER_*` variables passed
  through from the environment.
- CI builds the image only; no workflow change is needed.

### Documentation

- `AGENTS.md`: the clusterer row becomes a cron service; `portfolio-builder` depends on
  `ktb-core` and `ktb-market-analyzer` only; the "Services communicate through datastores"
  bullet drops the HTTP exception.
- The monorepo design spec keeps its history; this spec supersedes its §2 "one
  synchronous edge".

## 8. Limits and when to replace the algorithm

- **Every run is O(n²) in time.** At `n` articles it computes `n²` dot products of 2000
  dimensions. Memory is `n × 2000 × 4` bytes for the vectors (about 800 MB at 100,000
  articles) plus one row block of distances.
- **Replace with incremental DBSCAN or online learning** when a run no longer finishes
  well inside the cron interval or no longer fits the task's memory, judged from the §3.1 log line. Until then, full
  recompute stays.
- **Default `eps` / `min_samples` are guesses.** They need tuning against real
  embeddings; a change simply takes effect on the next full run.

## 9. Testing

- `dbscan` against `sklearn.cluster.DBSCAN(metric="cosine")` on generated unit vectors
  (several tight blobs plus scattered noise, seeded):
  - the noise set equals sklearn's `-1` points;
  - labels are equal up to renaming (identical partitions), including chains whose end
    points are border points;
  - run for several `(eps, min_samples)` pairs, including `min_samples = 1` (no noise)
    and an `eps` so small that everything is noise.
  Generated data keeps pairwise distances away from `eps`, so float rounding cannot flip
  a neighbour decision.
- `match`: unchanged clusters keep their ids; a grown cluster keeps its id; a split keeps
  the id on the larger part; a merge keeps the id with the larger overlap and deletes the
  other; ties follow the stated order.
- `storage` (PostgreSQL fixtures, skipped without `KTB_TEST_POSTGRES_DSN`): the §5 write
  steps including an article that becomes noise; the needs-summary query.
- `summarize`: `httpx.MockTransport` for a valid reply, malformed JSON and an HTTP error;
  the character budget keeps the newest article.
- `main`: end to end against PostgreSQL with a mocked LLM; a second run with no new
  articles writes nothing and makes no LLM call; the `clustering cost:` line is logged
  with the right `articles` / `clusters` / `noise` counts.
