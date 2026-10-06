# news-http design

## Goal

The Backend needs to show news to users: the news clusters about a stock, and the articles of a
cluster. Today that data exists only inside PostgreSQL, written by news-clusterer and
news-graph-builder. `services/news-http` exposes it over a small read-only HTTP API so the Backend
depends on a stable contract instead of our internal schema.

## Constraints

- Read-only. The service only runs `SELECT`s and adds no migrations.
- Private network, no authentication. The port is never published to the internet.
- Same stack as the other services: sync SQLAlchemy Core with psycopg, `pydantic-settings`,
  `ktb_core.logging.setup_logging()`. FastAPI runs on uvicorn; handlers are plain `def`, so
  FastAPI executes them in its threadpool.
- It is the first long-running service in the repo. Every other member is a one-shot job.

## Out of scope

- `compose.prod.yaml`. The service is added to `compose.dev.yaml` only; production placement waits
  until we know where the Backend runs.
- Article `body`, the knowledge graph, and search.

## API

Both list endpoints use keyset pagination for infinite scroll and share one response shape:

```json
{ "items": [ ... ], "next_cursor": "opaque string or null" }
```

- `limit`: default `20`, `1 ≤ limit ≤ 100`.
- `cursor`: omitted on the first page; afterwards the previous response's `next_cursor`.
- `next_cursor` is `null` when the page is the last one. The service fetches `limit + 1` rows to
  know this without a count query.
- The cursor is the sort key of the last returned row, `(timestamp, id)`, encoded as base64url
  of `"<ISO 8601 timestamp>|<id>"`. The Backend treats it as opaque. A cursor that does not decode
  returns `422`.

### `GET /stocks/{stock_code}/clusters`

Clusters that mention the stock, newest first.

```json
{
  "items": [
    {
      "id": 812,
      "title": "...",
      "summary": "...",
      "article_count": 7,
      "latest_published_at": "2026-10-05T09:12:00+09:00",
      "earliest_published_at": "2026-10-03T18:40:00+09:00"
    }
  ],
  "next_cursor": "MjAyNi0xMC0wNVQwMDoxMjowMCswMDowMHw4MTI"
}
```

- Path: `stock_code` → `entities.stock_code` → `cluster_entities` → `cluster_summaries`.
- `article_count`, `latest_published_at`, `earliest_published_at` are `count` / `max` / `min` of
  `articles.published_at` over `article_clusters`.
- Only clusters with a row in `cluster_summaries` are returned; an unsummarized cluster has
  nothing to show.
- Order: `latest_published_at DESC, id DESC`. Cursor filter:
  `(max(published_at), cluster_id) < (:ts, :id)` in `HAVING`.
- An unknown stock or a stock with no clusters returns `{"items": [], "next_cursor": null}`, not
  `404`.

### `GET /clusters/{cluster_id}/articles`

```json
{
  "items": [
    {
      "id": 90211,
      "title": "...",
      "url": "https://...",
      "source": "...",
      "published_at": "2026-10-05T09:12:00+09:00"
    }
  ],
  "next_cursor": null
}
```

- Path: `article_clusters.cluster_id` → `articles`.
- Order: `published_at DESC, id DESC`. Cursor filter: `(published_at, id) < (:ts, :id)`.
- A cluster that does not exist returns `404`. news-clusterer can delete a cluster on any run, so
  the Backend must expect `404` for a cluster id it saw earlier.

### `GET /health`

Returns `{"status": "ok"}` without touching the database. Used by the compose healthcheck.

### Pagination under concurrent writes

Clusters are re-matched every clusterer run, so the order can shift between page loads. A cluster
that gains a newer article moves above the cursor and is not repeated; it appears at the top on
the next refresh. A cluster that loses its newest article moves down and can appear twice. The
Backend de-duplicates items by `id`.

## Components

```
services/news-http/
  pyproject.toml            fastapi, uvicorn, sqlalchemy, psycopg[binary], pydantic-settings, ktb-core
  src/news_http/
    __main__.py             main(): setup_logging(), uvicorn.run(app, host, port)
    settings.py             NEWS_HTTP_ prefix
    app.py                  FastAPI app, routes, response models, cursor encode/decode
    storage.py              SQLAlchemy table definitions (only the columns read) and the two queries
  tests/
    conftest.py             engine on KTB_TEST_POSTGRES_DSN, skipped when unset; TRUNCATE between tests
    test_app.py             TestClient against the seeded database
    test_settings.py
```

The engine is created once at startup from the settings and stored on `app.state`; each request
opens one connection.

### Settings

| Variable | Default |
|---|---|
| `NEWS_HTTP_POSTGRES_DSN` | required |
| `NEWS_HTTP_HOST` | `0.0.0.0` |
| `NEWS_HTTP_PORT` | `8000` |
| `NEWS_HTTP_LOG_LEVEL` | `INFO` |

## Packaging

- `docker/news-http.Dockerfile`, same shape as the other services, `EXPOSE 8000`,
  `CMD ["news-http"]`.
- `docker/requirements/news-http.txt` and a regenerated `docker/requirements/app.txt` via the
  `uv export` commands in AGENTS.md.
- `./services/news-http` added to the `uv pip install` line of `docker/app.Dockerfile`.
- `compose.dev.yaml`: a `news-http` service with no profile (it is a server, so `up -d` starts
  it), `ports: ["8000:8000"]`, `NEWS_HTTP_POSTGRES_DSN` pointing at `postgres`,
  `depends_on: postgres: service_healthy`, `restart: unless-stopped`, and a healthcheck on
  `/health` using Python's `urllib` (the slim image has no curl).
- AGENTS.md and README.md: env var rows, the member row, and the run command.

## Testing

DB tests use the existing `KTB_TEST_POSTGRES_DSN` pattern and are skipped when it is unset. They
seed a corporation, an entity with its `stock_code`, clusters with and without summaries, and
articles with distinct `published_at`, then assert:

- clusters for a stock: fields and aggregates, newest-first order, unsummarized clusters excluded,
  unknown stock → empty page;
- articles of a cluster: fields, no `body`, order, unknown cluster → `404`;
- pagination on both endpoints: walking `next_cursor` with a small `limit` returns every row once
  and ends with `null`; ties on the timestamp are broken by `id`;
- a malformed cursor → `422`; `limit` outside `1..100` → `422`.
