# news-preprocessor — RSS Scraper + Embedding

**Date:** 2026-09-22
**Status:** Approved for planning

## 1. Purpose and scope

Turn the `news-preprocessor` skeleton into a working cron job: collect articles from two
Korean economy RSS feeds, fetch each article's body, store it in PostgreSQL, and attach a
Qwen3-Embedding-8B vector (served by vLLM) so `news-clusterer` can compare articles.

The feed handling and body parser are adapted from an earlier personal prototype. **This
spec is self-contained**: that prototype may become private or be deleted, so every fact
this work depends on — feed URLs, item fields, body selectors, parser algorithm, and the
prototype decisions kept or reversed — is recorded here (§2.1, §3.2) and was re-verified
against the live sites on 2026-09-22. Nothing in the implementation or its tests may
import, vendor, or link to the prototype.

### Non-goals

- LLM event extraction, summarisation, or any generative model call.
- Clustering, search endpoints, or any read path — that is `news-clusterer`'s job.
- Chunked (per-passage) embeddings.
- Sources beyond Hankyung economy and Maeil Business economy.
- Deployment mechanics (ECS task, schedule, Tailscale sidecar). Requirements on them are
  recorded in §9.

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Sources | Hankyung economy, Maeil Business economy | The two sites this product targets |
| Body extraction | Per-site CSS class, stdlib `html.parser` | No new dependency; two sites don't justify a generic extractor |
| Embedding backend | vLLM, OpenAI-compatible `POST /v1/embeddings` | Already serving the model on the embedding host |
| Embedding model | `mlx-community/Qwen3-Embedding-8B-4bit-DWQ` | Strong multilingual (Korean) quality |
| Embedding size | 2000: first 2000 of 4096 components, L2-renormalised, in the client | Qwen3 is Matryoshka-trained; 2000 is pgvector's HNSW limit for `vector`; the server is not configured for `dimensions` |
| Context | `EMBEDDING_MAX_TOKENS = 16384`, sent as `truncate_prompt_tokens` | Covers every measured article with room to spare; oversize outliers are truncated instead of failing a batch |
| Embedded text | `f"{title}\n\n{body}"`, one vector per article | Whole articles fit in 16k tokens; one vector per article is what clustering needs |
| DB access | SQLAlchemy Core (`Table` + expressions, no ORM) | Chosen during design |
| Failure model | Store first, embed separately; NULL embedding = pending | An embedding-server outage never loses articles that would otherwise age out of the feed |
| Shared embedding contract | Constants + client in `ktb_core.embedding` | Every service that embeds must produce comparable vectors |

### 2.1 Carried over from the prototype, and reversed

| Prototype decision | Here | Reason |
|---|---|---|
| Every source emits one normalised item; downstream code never branches per source | **Kept** (`NewsItem`) | Adding a source must not touch storage or embedding |
| Full body scraped immediately on discovery | **Kept** | RSS carries only a summary; pages can change or disappear |
| One adapter class + one parser class per site, explicitly "not a config-only URL swap", to exercise real structural differences | **Reversed**: one `RssSource`, two config instances | That rule served the prototype's goal of *validating the abstraction*. Here the goal is production ingestion, and the live differences between the two sites reduce to the body CSS class (§3.2). A site that needs different code gets its own class then. |
| Append-only `reference` table of every raw item seen, including duplicates, "since duplication frequency is itself a signal" | **Reversed**: first `raw_payload` only | No consumer of that signal exists or is planned in this repo. Re-adding it is an additive migration; nothing is lost by waiting except history from before it exists. |
| `pubDate` stored as the raw string | **Reversed**: `timestamptz` | Clustering needs time-ordering; see the `+09:00` pitfall in §3.2 |
| LLM event extraction with a 3-attempt retry state machine | **Dropped** | Non-goal (§1) |
| Adaptive polling toward a 1–10% new-item ratio | **Dropped** | The service is cron-triggered and exits; interval tuning is the scheduler's concern |
| Infrastructure hosts only in env vars, never in version control | **Kept** | The embedding host's address appears nowhere in this repo, this spec included |
| Known limitation: two RSS feeds prove parsing-level portability only, not other delivery models (push, non-prose, no per-item URL) | **Kept** | Still true; `NewsItem.url` is required here because both sources have one |

### 2.2 Embedding host — verified 2026-09-22

The host is a dedicated machine on the Tailscale tailnet, not the Fedora server. Its
address is supplied only through `KTB_EMBEDDING_BASE_URL`.

- vLLM 0.29.0 on `:8000` serves `mlx-community/Qwen3-Embedding-8B-4bit-DWQ`, returning
  4096-dimension vectors with L2 norm 1.0.
- `"dimensions": 2000` is **rejected** (400, "does not support Matryoshka embeddings")
  because the server was not started with `--hf-overrides '{"is_matryoshka": true}'`.
  Client-side truncate-and-renormalise is how Matryoshka truncation is defined (keep the
  leading components, re-normalise), and doing it in the one shared client makes the
  result independent of server flags. Not yet confirmed empirically against a
  server-side `dimensions` output — the host went down mid-check; the implementation
  plan includes that comparison as its first step.
- The server currently runs with `max_model_len` 1024. **The operator will restart it
  with `--max-model-len 16384`**; this spec assumes 16384.
- The same text embedded by vLLM and by Ollama (`qwen3-embedding:8b`) has cosine
  similarity 0.978, so the two backends' vectors must never share a table. The served
  model identifier — backend and quantisation included — is part of the schema contract.
- With the parser fix, 16 live articles (8 per site) have bodies of 193–2,205 characters,
  well under 16384 tokens.

## 3. Components

```
packages/core/src/ktb_core/
  embedding.py
    EMBEDDING_MODEL = "mlx-community/Qwen3-Embedding-8B-4bit-DWQ"
    EMBEDDING_DIMENSIONS = 2000
    EMBEDDING_MAX_TOKENS = 16384
    EmbeddingSettings(BaseSettings)       env_prefix="KTB_", field: embedding_base_url
    embed(texts, *, base_url, timeout=120) -> list[list[float]]

services/news-preprocessor/src/news_preprocessor/
  __main__.py        main(): scrape → embed → exit code
  settings.py        existing + embed_batch_limit (default 100)
  sources/
    news_item.py     NewsItem dataclass
    article_body.py  ArticleBodyParser (ported)
    rss.py           fetch_bytes() + RssSource
    feeds.py         HANKYUNG, MAEIL
  storage.py         `articles` Table, insert_new(), known_external_ids(),
                     pending_embedding(), set_embedding()
```

### 3.1 `ktb_core.embedding`

The model name, dimension count and token limit are **constants, not settings**. The
first two are part of the database schema: changing either makes every stored vector
incomparable, and the dimension count is fixed in the column type. The token limit
decides which text a vector represents, so it must also match across services. A change must be a code change plus a
migration in one reviewed PR, never a per-service env var that can silently diverge.

`embedding_base_url` is deployment configuration and is read from
`KTB_EMBEDDING_BASE_URL` (the vLLM server's `http://<host>:8000`). It
uses the shared `KTB_` prefix, not a per-service one, because it names one shared host.
`EmbeddingSettings` is a standalone object a service instantiates only if it embeds —
not a base class services inherit, so the monorepo spec's "no shared settings base" rule
still holds.

`embed()` POSTs `{"model": EMBEDDING_MODEL, "input": texts, "truncate_prompt_tokens":
EMBEDDING_MAX_TOKENS}` to `{base_url}/v1/embeddings` using `urllib.request`. It orders
`response["data"]` by `index`, keeps each vector's first `EMBEDDING_DIMENSIONS`
components, and divides by their L2 norm. It raises `ValueError` if the number of vectors
differs from the number of inputs, or if any returned vector is shorter than
`EMBEDDING_DIMENSIONS`. HTTP and network errors propagate unchanged
(`urllib.error.URLError` and subclasses).

`truncate_prompt_tokens` is sent as the explicit value 16384, not `-1` ("model
maximum"). With `-1`, a server still running at 1024 would silently embed only each
article's opening; with 16384 it should reject the request instead, turning a
misconfigured server into a failed run. **Verify during implementation** that vLLM 0.29
rejects `truncate_prompt_tokens > max_model_len`; if it clamps silently instead, `embed()`
must check `GET /v1/models` → `max_model_len >= EMBEDDING_MAX_TOKENS` once per call.

This is the first shared client in `core`; `core` gains no new third-party dependency.

### 3.2 Sources

`NewsItem` (frozen dataclass): `source: str`, `external_id: str`, `url: str`,
`title: str`, `body: str`, `published_at: datetime` (timezone-aware),
`raw_payload: str`.

`fetch_bytes(url, timeout=30) -> bytes` — `urllib` GET with a
`User-Agent: ktb-news-preprocessor/0.1` header.

`ArticleBodyParser(body_class)` — collects the text inside the first element whose
`class` attribute contains `body_class` as a whole token, then collapses all whitespace
runs to single spaces. The complete algorithm:

```python
VOID_ELEMENTS = frozenset({"area", "base", "br", "col", "embed", "hr", "img",
                           "input", "link", "meta", "source", "track", "wbr"})

class ArticleBodyParser(HTMLParser):
    # _depth == 0: outside the body; > 0: nesting level inside it
    def handle_starttag(self, tag, attrs):
        if tag in VOID_ELEMENTS:
            return
        if self._depth:
            self._depth += 1
        elif self._body_class in (dict(attrs).get("class") or "").split():
            self._depth = 1

    def handle_endtag(self, tag):
        if tag in VOID_ELEMENTS:
            return
        if self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._depth:
            self._parts.append(data)

    # text = " ".join("".join(self._parts).split())
```

Pages are decoded as UTF-8 with `errors="replace"`.

**Void elements must be skipped in both handlers.** The prototype's version skipped
neither: `<img>`/`<br>`/`<input>` raised the depth and never lowered it, so capture ran to
the end of the page, footer and sitemap included (measured on live Maeil pages: 4,300–20,900
characters, versus 965–2,205 with the fix). Skipping only in `handle_starttag` is also
wrong: `html.parser` routes a self-closing `<br/>` through both handlers, so the end tag
would close the body early (measured: Hankyung bodies cut to 63–525 characters).

`RssSource` is one class for both sites (§2.1). It holds
`source`, `feed_url`, `body_class`, and a `fetch` callable (default `fetch_bytes`), and
exposes:

- `entries() -> list[FeedEntry]` — fetch and parse the feed only (`./channel/item`).
  `FeedEntry` holds `external_id` (`guid`, falling back to `link`), `url` (`link`),
  `title`, `published_at`, and `raw_payload` (the `<item>` element serialised with
  `ElementTree.tostring(..., encoding="unicode")`). All text fields are stripped. Entries
  missing `link`, `title` or `pubDate` are skipped and logged.
- `published_at` parsing: `email.utils.parsedate_to_datetime(pubDate)`, **after**
  rewriting a trailing `±HH:MM` offset to `±HHMM`. Maeil emits `+09:00`, which is not RFC
  822; `parsedate_to_datetime` accepts it but silently returns a **naive** datetime, and
  Postgres would read that as UTC — nine hours off. A result that is still naive raises
  `ValueError` and the entry is skipped and logged.
- `article(entry) -> NewsItem` — fetch the entry's page and extract the body. Raises
  `EmptyBodyError` if the extracted body is empty.

Splitting feed parsing from page fetching lets `main()` drop already-stored entries
before any page request is made.

`feeds.py`:

| Instance | `source` | `feed_url` | `body_class` |
|---|---|---|---|
| `HANKYUNG` | `hankyung_economy` | `https://www.hankyung.com/feed/economy` | `article-body` |
| `MAEIL` | `maeil_business_economy` | `https://www.mk.co.kr/rss/30100041/` | `news_cnt_detail_wrap` |

Live feed facts (2026-09-22). Both are RSS 2.0 with 50 items and **neither has a `guid`**,
so `external_id` is the article URL for both today; the `guid` preference only matters if
one is added.

| | Hankyung | Maeil |
|---|---|---|
| `<item>` children | `title`, `link`, `author`, `pubDate` | `no`, `title`, `link`, `category`, `author`, `pubDate`, `description`, `media:content` |
| `link` shape | `https://www.hankyung.com/article/202609220705g` | `https://www.mk.co.kr/news/economy/12159314` |
| `pubDate` shape | `Tue, 22 Sep 2026 15:01:06 +0900` | `Tue, 22 Sep 2026 14:37:38 +09:00` |
| Body container | `<div class="article-body">` | `<div class="news_cnt_detail_wrap">` inside `sec_body` |
| Body length (fixed parser, 8 articles) | 193–2,055 chars | 965–2,205 chars |

### 3.3 Storage

`storage.py` defines the `articles` `Table` mirroring the migration (§4) — used for
queries only, never for DDL. Functions take a SQLAlchemy `Connection`:

- `known_external_ids(conn, source, external_ids) -> set[str]`
- `insert_new(conn, item)` — `INSERT … ON CONFLICT (source, external_id) DO NOTHING`
- `pending_embedding(conn, limit) -> list[Row]` — `id, title, body` where
  `embedding IS NULL`, ordered by `id`
- `set_embedding(conn, ids, vectors)` — one `UPDATE` per row, inside the caller's
  transaction

Vector values use `pgvector.sqlalchemy.Vector(EMBEDDING_DIMENSIONS)`.

The engine is created in `main()` from `NEWS_PREPROCESSOR_POSTGRES_DSN` (already in
`Settings`). Per the monorepo spec, a Postgres connection factory moves to `core` when a
second service reads the table; it stays local until then.

## 4. Schema — migration `0001_create_articles`

Hand-written with `op.*`. `env.py` keeps `target_metadata = None`, so Alembic never
imports service code; the `migrations` dependency group gains only `pgvector`.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE articles (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source        text          NOT NULL,
  external_id   text          NOT NULL,
  url           text          NOT NULL,
  title         text          NOT NULL,
  body          text          NOT NULL,
  published_at  timestamptz   NOT NULL,
  raw_payload   text          NOT NULL,
  fetched_at    timestamptz   NOT NULL DEFAULT now(),
  embedding     vector(2000),
  CONSTRAINT articles_source_external_id_key UNIQUE (source, external_id)
);

CREATE INDEX articles_embedding_hnsw ON articles USING hnsw (embedding vector_cosine_ops);
CREATE INDEX articles_published_at_idx ON articles (published_at);
```

- `embedding IS NULL` means "pending embedding". There is no status column.
- `2000` is written literally in the migration because the `migrations` group does not
  depend on `core`. The metadata comparison test (§7) catches any drift from
  `EMBEDDING_DIMENSIONS`.
- Downgrade drops the table. It does not drop the `vector` extension, which other
  schemas may come to use.

Deliberately omitted: a raw-payload history table (the first `raw_payload` is kept; add
history when something reads it) and an `embedding_model` column (a model change already
requires a migration, because the dimension is in the column type).

## 5. Run flow

`main()` performs one pass and exits. It never loops or schedules; scheduling is the
platform's job.

```
1. settings, embedding settings, engine
2. SCRAPE — for each source in (HANKYUNG, MAEIL):
     entries = source.entries()                 failure → log, failed = True, next source
     known   = known_external_ids(...)          one query per source
     for entry not in known:
       item = source.article(entry)             failure / EmptyBodyError → log, failed = True, next entry
       insert_new(conn, item)                   committed per article
3. EMBED
     rows = pending_embedding(limit=settings.embed_batch_limit)
     for batch of 16 rows:
       vectors = embed([f"{title}\n\n{body}" …])   failure → log, failed = True, stop EMBED
       set_embedding(...)                           one transaction per batch
4. exit 1 if failed else 0 (via sys.exit)
```

## 6. Error handling

| Failure | Behaviour | Recovery |
|---|---|---|
| Feed unreachable / malformed XML | Log with source name; skip that source | Next run |
| Article page unreachable | Log with URL; skip the article | Next run, if still in the feed |
| Empty body | Log with source and URL; skip; run exits 1 | Almost always a markup change — fix `body_class` |
| Embedding server unreachable / HTTP error | Log; stop embedding for this run | Rows stay NULL; next run embeds them |
| Vector too short / count mismatch | `ValueError` from `embed()`; stop embedding | Configuration bug — fix the model or constant |
| Server context below 16384 | Request rejected (see §3.1); stop embedding | Restart vLLM with `--max-model-len 16384` |
| Duplicate insert from overlapping runs | `ON CONFLICT DO NOTHING` | None needed |
| Database unreachable | Unhandled; process exits non-zero with a traceback | Next run |

Embedding stops at the first failure because an unreachable host makes every later call
fail too; continuing would only add timeouts. Embeddings committed earlier in the run are
kept.

Timeouts: 30 s for feed and page fetches, 120 s for embedding (a batch of 16 full
articles through an 8B model on the host). No retries within a run — the next cron run is the retry.

The limit of `embed_batch_limit` (default 100) rows per run keeps a backlog, after an
outage or on the first run, from stretching one run past the next cron trigger. The
backlog drains over later runs.

## 7. Testing

| File | Checks | Needs |
|---|---|---|
| `packages/core/tests/test_embedding.py` | Request body contains `model` and `truncate_prompt_tokens=16384`, and no `dimensions`; vectors reordered by `index`; output is 2000 long with L2 norm 1.0 and equals the renormalised prefix; `ValueError` on short vector and on count mismatch | `urlopen` stub |
| `services/news-preprocessor/tests/test_sources.py` | Fixture feed + article HTML per site → expected entries and `NewsItem`s; `guid` → `link` fallback; `+0900` **and** `+09:00` both parse to `+09:00`-aware datetimes; entries missing fields skipped; `EmptyBodyError` on empty body; body stops at the container's end despite `<img>`, `<br>` and `<br/>` inside it and following page content (regression test for the parser fix) | fake `fetch`, fixtures |
| `services/news-preprocessor/tests/test_storage.py` | Duplicate insert yields one row; `known_external_ids`; `pending_embedding` returns only NULL rows, ordered, limited; `set_embedding` writes the vector | Postgres |
| `services/news-preprocessor/tests/test_main.py` | Full run with fake fetch + fake embed → embedded rows, exit 0; one feed fails → other source still processed, exit 1; embed fails → articles stored with NULL embedding, exit 1 | Postgres |
| `infrastructure/postgres/tests/test_migrations.py` | Existing tests, plus: after `upgrade head`, `compare_metadata` against the service's `articles` Table reports no differences | Postgres |

The existing `test_main.py` (which asserts the skeleton exits 0) is replaced.

Fixtures live in `services/news-preprocessor/tests/fixtures/` and are **synthetic**: they
reproduce each site's structure from §3.2 (item children, `link` and `pubDate` shapes,
body container class, nesting, void elements, trailing page chrome) with invented Korean
text. Real article text is not committed — it is the publishers' copyrighted content, and
the repository may be public.

Tests that need Postgres read `KTB_TEST_POSTGRES_DSN` and are skipped when it is unset, so
a local `pytest` works without Docker. CI always sets it. `test_storage.py` runs each test in a transaction that is rolled
back; `test_main.py` calls `main()`, which commits, so it truncates `articles` before each test.

No automated test calls the real news sites or the real embedding host. Before
completion, one manual end-to-end run is made against the compose Postgres and the vLLM
server,
confirming rows with non-NULL 2000-dimension embeddings.

## 8. Infrastructure and CI changes

- **`compose.dev.yaml`:** `postgres` image changes from `postgres:18.6-trixie` to
  `pgvector/pgvector:0.8.6-pg18-trixie`. Everything else is unchanged.
- **Dependencies:**
  - `news-preprocessor`: `sqlalchemy`, `psycopg[binary]`, `pgvector`
  - root `migrations` group: `pgvector`
  - `core`: none
  - Regenerate `uv.lock` and `docker/requirements/*.txt`.
- **CI `lint-types-tests`:** add a `pgvector/pgvector:0.8.6-pg18-trixie` service
  container, set `KTB_POSTGRES_DSN` and `KTB_TEST_POSTGRES_DSN`, and run
  `uv run alembic upgrade head` before `pytest`.
- **CI `python-314-compatibility`:** same Postgres service and env, so it tests the same
  code paths.
- **CI `build-and-run-images`:** the preprocessor now does real work and would exit 1
  under `--network none`. Replace its step with
  `docker run --rm --network none --entrypoint python ktb-news-preprocessor -c "import news_preprocessor.__main__"`,
  which keeps the step's purpose (the image carries every runtime dependency).
  `portfolio-builder` and `news-clusterer` steps are unchanged.

## 9. Deployment requirements (recorded, not implemented)

- Production runs on AWS; the embedding host is a dedicated machine on the
  Tailscale tailnet (not the Fedora server). The task must join the tailnet — for
  example, a Tailscale sidecar in the task or a subnet router in the VPC.
- vLLM is started without `--api-key`, so it has no authentication. A Tailscale ACL must
  restrict `:8000` on the embedding host to the preprocessor's node (and later other
  embedding consumers) only. Ollama's `:11434` on the same host is no longer used by this
  repo and should not be opened to these nodes.
- vLLM runs with `--max-model-len 16384`.
- Environment: `NEWS_PREPROCESSOR_POSTGRES_DSN`, `KTB_EMBEDDING_BASE_URL`, optionally
  `NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT` and `NEWS_PREPROCESSOR_LOG_LEVEL`.
- `alembic upgrade head` must run as a separate job before the new image is scheduled.

## 10. Acceptance criteria

1. `alembic upgrade head` against an empty compose Postgres creates `articles`, both
   indexes, and the `vector` extension. `alembic downgrade base` removes the table.
2. `uv run pytest` passes with `KTB_TEST_POSTGRES_DSN` set, and skips only the
   database-backed tests when it is unset.
3. `ruff check`, `ruff format --check`, and `ty check` pass.
4. Every CI job passes on the PR (`py314` still allowed to fail).
5. Manual run: `uv run news-preprocessor` against the compose Postgres and the vLLM
   server (restarted at 16384) exits 0 and leaves rows from both sources whose `vector_dims(embedding)` is 2000.
6. Running it a second time right away inserts no duplicate rows.
7. With `KTB_EMBEDDING_BASE_URL` pointing at an unreachable address, a run stores new
   articles with a NULL embedding and exits 1. A following run with the correct URL fills
   them in.
