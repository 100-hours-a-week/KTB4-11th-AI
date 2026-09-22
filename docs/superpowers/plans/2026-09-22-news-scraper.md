# news-preprocessor RSS Scraper + Embedding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `news-preprocessor` skeleton into a cron job that scrapes the Hankyung and Maeil Business economy RSS feeds, stores each article in PostgreSQL, and attaches a 2000-dimension Qwen3-Embedding-4B vector served by vLLM.

**Architecture:** One pass per run: each publisher adapter reads its feed, skips entries already stored, fetches and parses new article pages, and inserts them; then every row whose `embedding IS NULL` (up to a limit) is embedded in batches of 16 and updated. The shared embedding contract (model, dimensions, token limit, client) lives in `ktb_core.embedding`, standard library only. The `articles` schema is owned by an Alembic migration; the service mirrors it as a SQLAlchemy Core `Table` for queries only.

**Tech Stack:** Python 3.13, uv workspace, SQLAlchemy 2 Core + psycopg 3, pgvector 0.8.6 (Postgres 18) and `pgvector` 0.5 (Python), Alembic, pydantic-settings, stdlib `urllib` / `html.parser` / `xml.etree`, vLLM 0.29 OpenAI-compatible `/v1/embeddings`, pytest, ruff, ty, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-22-news-scraper-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Embedding contract, verbatim:** `EMBEDDING_MODEL = "mlx-community/Qwen3-Embedding-4B-4bit-DWQ"`, `EMBEDDING_DIMENSIONS = 2000`, `EMBEDDING_MAX_TOKENS = 16384`, `EMBEDDING_BASE_URI_ENV = "KTB_EMBEDDING_BASE_URI"`. The client truncates 2560 → 2000 and re-normalises itself; it never sends `dimensions`. It always sends `truncate_prompt_tokens: 16384` (never `-1`).
- **`KTB_EMBEDDING_BASE_URI` includes `/v1`** (e.g. `http://100.77.120.106:8000/v1`); the client POSTs to `{base}/embeddings`.
- **`packages/core` stays standard-library only.** No `pydantic-settings` there. Services declare `embedding_base_uri: str = Field(validation_alias=EMBEDDING_BASE_URI_ENV)` in their own `Settings`.
- **DSNs use the `postgresql+psycopg://` scheme** everywhere (settings, tests, CI, Alembic). A bare `postgresql://` makes SQLAlchemy load psycopg2, which is not installed.
- **The migration owns the schema.** `infrastructure/postgres/migrations/env.py` keeps `target_metadata = None`. The service `Table` never issues DDL. A test compares the two (Task 6).
- **One adapter class + one parser class per publisher** under `news_preprocessor/sources/publishers/<outlet>/` — never a shared config-driven adapter.
- **`--locked`, never `--frozen`.** After any dependency change: `uv lock`, then regenerate all three exported requirement files (the `verify-exported-requirements` CI job diffs them):
  ```bash
  for pkg in news-preprocessor news-clusterer portfolio-builder; do
    uv export --package "$pkg" --no-dev --no-emit-workspace --format requirements-txt -o "docker/requirements/${pkg}.txt"
  done
  ```
- **No automated test touches the network.** Feeds, pages and the embedding server are faked. Fixtures are synthetic — never commit real article text.
- **Tests that need Postgres** use the `pg_dsn` / `pg_engine` / `pg_conn` fixtures from the root `conftest.py`, which skip when `KTB_TEST_POSTGRES_DSN` is unset. Point it at a dedicated database: the tests `TRUNCATE articles` and run `alembic downgrade base`.
- **Before every commit:** `uv run ruff check . && uv run ruff format --check . && uv run ty check` and `uv run pytest` pass.
- **Commit at the end of every task.** Messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

### Local database setup (once, before Task 1's database tests)

```bash
docker compose -f compose.dev.yaml up -d postgres          # after Task 1 Step 1 switches the image
docker compose -f compose.dev.yaml exec postgres createdb -U ktb news_test   # skip if it exists
export KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test
```

Switching the compose image from `postgres:18.6-trixie` to `pgvector/pgvector:0.8.6-pg18-trixie` recreates the container on the same `postgres-data` volume; both are PostgreSQL 18.6, so the data directory is compatible.

---

## File Structure

| Path | Responsibility |
|---|---|
| `compose.dev.yaml` | Dev Postgres image → pgvector build. |
| `pyproject.toml` | `migrations` group gains `pgvector` (the migration imports `pgvector.sqlalchemy`). |
| `conftest.py` (repo root) | `pg_dsn`, `pg_engine`, `pg_conn` fixtures shared by every package's tests. |
| `infrastructure/postgres/migrations/versions/0001_create_articles.py` | `vector` extension, `articles` table, HNSW + `published_at` indexes. |
| `infrastructure/postgres/tests/test_migrations.py` | Upgrade/downgrade round-trip; service `Table` matches migrated schema. |
| `packages/core/src/ktb_core/embedding.py` | Embedding contract constants + `embed()` client (stdlib). |
| `services/news-preprocessor/src/news_preprocessor/sources/` (excluding `publishers/`) | Shared types (`FeedEntry`, `NewsItem`, `EmptyBodyError`, `NewsSource`), `fetch_bytes`, `ArticleBodyParser`. |
| `services/news-preprocessor/src/news_preprocessor/sources/publishers/hankyung/` | `HankyungEconomyRSS` + `HankyungEconomyParser`. |
| `services/news-preprocessor/src/news_preprocessor/sources/publishers/maeil/` | `MaeilBusinessEconomyRSS` + `MaeilBusinessEconomyParser` (`+09:00` fix). |
| `services/news-preprocessor/src/news_preprocessor/sources/publishers/__init__.py` | `SOURCES` — the adapters one run iterates. |
| `services/news-preprocessor/src/news_preprocessor/storage.py` | `articles` Table mirror + four query functions. |
| `services/news-preprocessor/src/news_preprocessor/settings.py` | + `embedding_base_uri`, `embed_batch_limit`. |
| `services/news-preprocessor/src/news_preprocessor/__main__.py` | `run()` (scrape → embed, returns ok) and `main()` (wiring + exit code). |
| `services/news-preprocessor/tests/fixtures/` | Synthetic feed XML and article HTML per publisher. |
| `.github/workflows/ci.yaml` | Postgres service for test jobs; preprocessor image step becomes an import check. |

---

### Task 1: pgvector Postgres, the `articles` migration, and database test fixtures

**Files:**
- Modify: `compose.dev.yaml` (postgres `image:`)
- Modify: `pyproject.toml` (`[dependency-groups] migrations`), `uv.lock`
- Create: `conftest.py`
- Create: `infrastructure/postgres/migrations/versions/0001_create_articles.py`
- Modify: `infrastructure/postgres/tests/test_migrations.py`
- Modify: `.github/workflows/ci.yaml` (`lint-types-tests`, `python-314-compatibility`)

**Interfaces:**
- Consumes: nothing new. `env.py` already reads `KTB_POSTGRES_DSN`.
- Produces: table `articles` (columns `id, source, external_id, url, title, body, published_at, raw_payload, fetched_at, embedding vector(2000)`; constraint `articles_source_external_id_key`; indexes `articles_embedding_hnsw`, `articles_published_at_idx`). Pytest fixtures `pg_dsn() -> str`, `pg_engine() -> sa.Engine` (session), `pg_conn() -> sa.Connection` (per test, rolled back).

- [ ] **Step 1: Switch the dev database image and add `pgvector` to the migrations group**

In `compose.dev.yaml`, change the postgres service's image line to:

```yaml
    image: pgvector/pgvector:0.8.6-pg18-trixie
```

In the root `pyproject.toml`:

```toml
migrations = [
    "alembic>=1.16",
    "psycopg[binary]>=3.3.6",
    "pgvector>=0.5.0",
]
```

Run:

```bash
uv lock
uv sync --all-packages --locked --group migrations
docker compose -f compose.dev.yaml up -d postgres
```

Then do the **Local database setup** above.

- [ ] **Step 2: Add the shared database fixtures**

`conftest.py`:

```python
"""Database fixtures shared by every package's tests."""

import os

import pytest
import sqlalchemy as sa

TEST_DSN_ENV = "KTB_TEST_POSTGRES_DSN"


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = os.environ.get(TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"{TEST_DSN_ENV} is not set")
    return dsn


@pytest.fixture(scope="session")
def pg_engine(pg_dsn):
    engine = sa.create_engine(pg_dsn)
    yield engine
    engine.dispose()


@pytest.fixture
def pg_conn(pg_engine):
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()
```

- [ ] **Step 3: Write the failing migration tests**

Replace `infrastructure/postgres/tests/test_migrations.py` with (the first four tests are unchanged):

`infrastructure/postgres/tests/test_migrations.py`:

```python
import configparser
import pathlib

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REPO_ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / "alembic.ini").is_file()
)
MIGRATIONS_DIR = "infrastructure/postgres/migrations"


def _alembic_config_without_interpolation() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    parser.read(REPO_ROOT / "alembic.ini")
    return parser


def test_alembic_ini_lives_at_the_repository_root():
    assert (REPO_ROOT / "alembic.ini").is_file()


def test_script_location_points_at_infrastructure():
    location = _alembic_config_without_interpolation()["alembic"]["script_location"]

    assert location.endswith(MIGRATIONS_DIR)


def test_no_database_url_is_committed():
    assert "sqlalchemy.url" not in _alembic_config_without_interpolation()["alembic"]


def test_versions_directory_exists():
    assert (REPO_ROOT / MIGRATIONS_DIR / "versions").is_dir()


def _alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def _embedding_column_type(conn) -> str | None:
    return conn.execute(
        sa.text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = to_regclass('articles') AND attname = 'embedding'"
        )
    ).scalar()


def test_upgrade_creates_articles_with_a_2000_dimension_vector(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)

    command.upgrade(_alembic_config(), "head")

    with pg_engine.connect() as conn:
        assert _embedding_column_type(conn) == "vector(2000)"
        indexes = set(
            conn.execute(
                sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'articles'")
            ).scalars()
        )
    assert {"articles_embedding_hnsw", "articles_published_at_idx"} <= indexes


def test_downgrade_removes_articles_and_upgrade_restores_it(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()

    command.downgrade(config, "base")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('articles')")).scalar() is None

    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        assert _embedding_column_type(conn) == "vector(2000)"
```

- [ ] **Step 4: Run them to verify they fail**

Run: `uv run pytest infrastructure -v`
Expected: the two new tests FAIL — `assert None == 'vector(2000)'` (no revision creates the table yet). The four existing tests pass.

- [ ] **Step 5: Write the migration**

`infrastructure/postgres/migrations/versions/0001_create_articles.py`:

```python
"""create articles

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", sa.Text, nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("embedding", VECTOR(2000), nullable=True),
        sa.UniqueConstraint("source", "external_id", name="articles_source_external_id_key"),
    )
    op.create_index(
        "articles_embedding_hnsw",
        "articles",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("articles_published_at_idx", "articles", ["published_at"])


def downgrade() -> None:
    op.drop_table("articles")
```

- [ ] **Step 6: Run the tests to verify they pass, and that they skip without a database**

Run: `uv run pytest infrastructure -v`
Expected: 6 passed.

Run: `env -u KTB_TEST_POSTGRES_DSN uv run pytest infrastructure -rs`
Expected: 4 passed, 2 skipped (`KTB_TEST_POSTGRES_DSN is not set`).

Leave the test database migrated for later tasks. Pytest runs `infrastructure` last (`testpaths` order), so service tests added in Tasks 6–7 must find the table already there; the round-trip test always ends at `head`:

```bash
KTB_POSTGRES_DSN=$KTB_TEST_POSTGRES_DSN uv run alembic upgrade head
```

- [ ] **Step 7: Give the CI test jobs a Postgres**

In `.github/workflows/ci.yaml`, insert this block between `runs-on`/`continue-on-error` and `steps:` in **both** `lint-types-tests` and `python-314-compatibility`:

```yaml
    services:
      postgres:
        image: pgvector/pgvector:0.8.6-pg18-trixie
        env:
          POSTGRES_USER: ktb
          POSTGRES_PASSWORD: ktb
          POSTGRES_DB: news
        ports:
          - "5432:5432"
        options: >-
          --health-cmd "pg_isready -U ktb -d news"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 12
    env:
      KTB_POSTGRES_DSN: postgresql+psycopg://ktb:ktb@localhost:5432/news
      KTB_TEST_POSTGRES_DSN: postgresql+psycopg://ktb:ktb@localhost:5432/news
```

In `lint-types-tests`, add `- run: uv run alembic upgrade head` immediately before `- run: uv run pytest`. In `python-314-compatibility`, add `- run: uv run --python 3.14 alembic upgrade head` immediately before `- run: uv run --python 3.14 pytest`.

(CI uses one database for both DSNs; that is safe there because the runner is disposable.)

- [ ] **Step 8: Lint, type-check, full suite**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add compose.dev.yaml pyproject.toml uv.lock conftest.py infrastructure/postgres \
  .github/workflows/ci.yaml
git commit -m "feat: add articles migration with pgvector and database test fixtures

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The shared embedding contract and client in `ktb_core`

**Files:**
- Create: `packages/core/src/ktb_core/embedding.py`
- Test: `packages/core/tests/test_embedding.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ktb_core.embedding.EMBEDDING_MODEL: str`, `EMBEDDING_DIMENSIONS: int` (2000), `EMBEDDING_MAX_TOKENS: int` (16384), `EMBEDDING_BASE_URI_ENV: str` (`"KTB_EMBEDDING_BASE_URI"`), and `embed(texts: list[str], *, base_uri: str, timeout: float = 120) -> list[list[float]]`. Raises `ValueError` on a wrong vector count or a vector shorter than 2000; `urllib.error.URLError`/`HTTPError` propagate.

- [ ] **Step 1: Write the failing tests**

`packages/core/tests/test_embedding.py`:

```python
import io
import json
import math

import pytest
from ktb_core import embedding
from ktb_core.embedding import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, embed

NATIVE_DIMENSIONS = 2560


def _vector(first: float, second: float) -> list[float]:
    return [first, second] + [0.0] * (NATIVE_DIMENSIONS - 2)


@pytest.fixture
def server(monkeypatch):
    """Replace urlopen with a fake server; returns the list of captured requests."""
    requests = []
    responses = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps(responses.pop(0)).encode())

    monkeypatch.setattr(embedding, "urlopen", fake_urlopen)
    return requests, responses


def test_posts_the_contract_to_the_openai_embeddings_route(server):
    requests, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    embed(["기준금리 동결"], base_uri="http://embedder:8000/v1/")

    request, timeout = requests[0]
    assert request.full_url == "http://embedder:8000/v1/embeddings"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {
        "model": EMBEDDING_MODEL,
        "input": ["기준금리 동결"],
        "truncate_prompt_tokens": 16384,
    }
    assert timeout == 120


def test_truncates_to_2000_dimensions_and_renormalises(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(3.0, 4.0)}]})

    [vector] = embed(["x"], base_uri="http://embedder:8000/v1")

    assert len(vector) == EMBEDDING_DIMENSIONS
    assert vector[:2] == [0.6, 0.8]
    assert math.isclose(math.hypot(*vector), 1.0)


def test_returns_vectors_in_input_order(server):
    _, responses = server
    responses.append(
        {
            "data": [
                {"index": 1, "embedding": _vector(0.0, 1.0)},
                {"index": 0, "embedding": _vector(1.0, 0.0)},
            ]
        }
    )

    first, second = embed(["a", "b"], base_uri="http://embedder:8000/v1")

    assert first[:2] == [1.0, 0.0]
    assert second[:2] == [0.0, 1.0]


def test_rejects_a_vector_shorter_than_the_contract(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": [1.0] * 1024}]})

    with pytest.raises(ValueError, match="2000"):
        embed(["x"], base_uri="http://embedder:8000/v1")


def test_rejects_a_response_with_the_wrong_number_of_vectors(server):
    _, responses = server
    responses.append({"data": [{"index": 0, "embedding": _vector(1.0, 0.0)}]})

    with pytest.raises(ValueError, match="expected 2 embeddings"):
        embed(["a", "b"], base_uri="http://embedder:8000/v1")


def test_empty_input_makes_no_request(server):
    requests, _ = server

    assert embed([], base_uri="http://embedder:8000/v1") == []
    assert requests == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest packages/core/tests/test_embedding.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ktb_core.embedding'`.

- [ ] **Step 3: Implement the client**

`packages/core/src/ktb_core/embedding.py`:

```python
"""The shared embedding contract and its OpenAI-compatible client.

The model, dimension count and token limit are part of the database schema: vectors
produced with any other values are not comparable with stored ones. Change them only
together with a migration.
"""

import json
import math
from urllib.request import Request, urlopen

EMBEDDING_MODEL = "mlx-community/Qwen3-Embedding-4B-4bit-DWQ"
EMBEDDING_DIMENSIONS = 2000
EMBEDDING_MAX_TOKENS = 16384
EMBEDDING_BASE_URI_ENV = "KTB_EMBEDDING_BASE_URI"


def embed(texts: list[str], *, base_uri: str, timeout: float = 120) -> list[list[float]]:
    """Embed texts in order via `POST {base_uri}/embeddings`.

    Each vector is cut to its first EMBEDDING_DIMENSIONS components and re-normalised to
    unit length (Matryoshka truncation).
    """
    if not texts:
        return []
    payload = {
        "model": EMBEDDING_MODEL,
        "input": texts,
        "truncate_prompt_tokens": EMBEDDING_MAX_TOKENS,
    }
    request = Request(
        f"{base_uri.rstrip('/')}/embeddings",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.load(response)["data"]
    if len(data) != len(texts):
        raise ValueError(f"expected {len(texts)} embeddings, got {len(data)}")
    return [_truncate(item["embedding"]) for item in sorted(data, key=lambda item: item["index"])]


def _truncate(vector: list[float]) -> list[float]:
    if len(vector) < EMBEDDING_DIMENSIONS:
        raise ValueError(f"expected at least {EMBEDDING_DIMENSIONS} dimensions, got {len(vector)}")
    head = vector[:EMBEDDING_DIMENSIONS]
    norm = math.hypot(*head)
    return [component / norm for component in head]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest packages/core -v`
Expected: 11 passed (5 logging + 6 embedding).

- [ ] **Step 5: Confirm core still has no third-party dependencies**

Run: `grep -A2 '^dependencies' packages/core/pyproject.toml`
Expected: `dependencies = []` — unchanged.

- [ ] **Step 6: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`

```bash
git add packages/core
git commit -m "feat(core): add the shared embedding contract and OpenAI-compatible client

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Shared source types and the fixed article body parser

**Files:**
- Create: `services/news-preprocessor/src/news_preprocessor/sources/__init__.py`, `news_item.py`, `empty_body_error.py`, `news_source.py`, `http.py`, `article_body_parser.py`
- Test: `services/news-preprocessor/tests/test_article_body_parser.py`

**Interfaces:**
- Consumes: nothing.
- Produces (all importable from `news_preprocessor.sources`):
  - `FeedEntry(source: str, external_id: str, url: str, title: str, published_at: datetime, raw_payload: str)` — frozen dataclass.
  - `NewsItem(FeedEntry)` — adds `body: str`. Its fields are exactly the `articles` columns a scrape writes, so `asdict(item)` is an insert row.
  - `EmptyBodyError(Exception)`.
  - `NewsSource` Protocol: `source: str`, `entries() -> list[FeedEntry]`, `article(entry: FeedEntry) -> NewsItem`.
  - `fetch_bytes(url: str, timeout: float = 30) -> bytes`.
  - `ArticleBodyParser(body_class: str)` with `.text -> str`; `parse_article_text(html: bytes, parser: ArticleBodyParser) -> str`.

- [ ] **Step 1: Write the failing parser tests**

`services/news-preprocessor/tests/test_article_body_parser.py`:

```python
from news_preprocessor.sources import ArticleBodyParser, parse_article_text


def _parse(html: str, body_class: str = "body") -> str:
    return parse_article_text(html.encode(), ArticleBodyParser(body_class))


def test_collects_only_the_body_and_collapses_whitespace():
    html = '<p>메뉴</p><div class="body">\n  첫 문장.\n\n  <b>둘째</b> 문장.  </div><p>푸터</p>'

    assert _parse(html) == "첫 문장. 둘째 문장."


def test_a_void_element_inside_the_body_does_not_extend_capture_past_its_end():
    html = (
        '<div class="body">본문<img src="a.jpg"><input type="hidden"></div>'
        "<footer>사이트맵</footer>"
    )

    assert _parse(html) == "본문"


def test_a_self_closing_br_does_not_end_capture_early():
    html = '<div class="body">앞 문장<br/>뒤 문장<br>끝</div>'

    assert _parse(html) == "앞 문장뒤 문장끝"


def test_only_the_first_matching_element_is_captured():
    html = '<div class="body">기사</div><div class="body">추천 기사</div>'

    assert _parse(html) == "기사"


def test_matches_a_whole_class_token_only():
    html = '<div class="body-wrap">광고</div><div class="main body">기사</div>'

    assert _parse(html) == "기사"


def test_returns_empty_text_when_the_class_is_absent():
    assert _parse('<div class="other">기사</div>') == ""


def test_decodes_invalid_utf8_with_replacement():
    parser = ArticleBodyParser("body")

    assert parse_article_text(b'<div class="body">\xff</div>', parser) == "�"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest services/news-preprocessor/tests/test_article_body_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'news_preprocessor.sources'`.

- [ ] **Step 3: Write the shared types**

`services/news-preprocessor/src/news_preprocessor/sources/news_item.py`:

```python
"""The normalised records every publisher adapter emits."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FeedEntry:
    """One RSS item, before its article page is fetched."""

    source: str
    external_id: str
    url: str
    title: str
    published_at: datetime
    raw_payload: str


@dataclass(frozen=True)
class NewsItem(FeedEntry):
    """A feed entry with its scraped article body."""

    body: str
```

`services/news-preprocessor/src/news_preprocessor/sources/empty_body_error.py`:

```python
"""Raised when an article page yields no body text."""


class EmptyBodyError(Exception):
    """The page had no body text — almost always a change in the site's markup."""
```

`services/news-preprocessor/src/news_preprocessor/sources/news_source.py`:

```python
"""The interface every publisher adapter implements."""

from typing import Protocol

from news_preprocessor.sources.news_item import FeedEntry, NewsItem


class NewsSource(Protocol):
    source: str

    def entries(self) -> list[FeedEntry]:
        """Fetch and parse the feed only; no article pages are requested."""
        ...

    def article(self, entry: FeedEntry) -> NewsItem:
        """Fetch the entry's page and extract its body. Raises EmptyBodyError."""
        ...
```

`services/news-preprocessor/src/news_preprocessor/sources/http.py`:

```python
"""The HTTP GET every adapter uses unless a test injects its own."""

from urllib.request import Request, urlopen

USER_AGENT = "ktb-news-preprocessor/0.1"


def fetch_bytes(url: str, timeout: float = 30) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return response.read()
```

- [ ] **Step 4: Write the parser**

Void elements must be ignored in **both** handlers. Ignoring neither (the prototype's bug) lets `<img>`/`<br>` raise the depth forever, so capture runs to the end of the page. Ignoring only the start tag lets a self-closing `<br/>` — which `html.parser` routes through both handlers — close the body early. The `_done` flag keeps capture to the first matching element.

`services/news-preprocessor/src/news_preprocessor/sources/article_body_parser.py`:

```python
"""Extracts an article's text from the first element carrying one CSS class."""

from html.parser import HTMLParser

VOID_ELEMENTS = frozenset("area base br col embed hr img input link meta source track wbr".split())


class ArticleBodyParser(HTMLParser):
    """Collects text inside the first element whose class list contains `body_class`.

    Void elements never close, so they must not move the nesting depth — in either
    handler, because `html.parser` routes a self-closing `<br/>` through both.
    """

    def __init__(self, body_class: str) -> None:
        super().__init__()
        self._body_class = body_class
        self._depth = 0
        self._done = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_ELEMENTS or self._done:
            return
        if self._depth:
            self._depth += 1
        elif self._body_class in (dict(attrs).get("class") or "").split():
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_ELEMENTS or not self._depth:
            return
        self._depth -= 1
        if not self._depth:
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self._parts).split())


def parse_article_text(html: bytes, parser: ArticleBodyParser) -> str:
    parser.feed(html.decode("utf-8", errors="replace"))
    parser.close()
    return parser.text
```

`services/news-preprocessor/src/news_preprocessor/sources/__init__.py`:

```python
"""Types and helpers shared by every news publisher adapter."""

from news_preprocessor.sources.article_body_parser import ArticleBodyParser, parse_article_text
from news_preprocessor.sources.empty_body_error import EmptyBodyError
from news_preprocessor.sources.http import fetch_bytes
from news_preprocessor.sources.news_item import FeedEntry, NewsItem
from news_preprocessor.sources.news_source import NewsSource

__all__ = [
    "ArticleBodyParser",
    "EmptyBodyError",
    "FeedEntry",
    "NewsItem",
    "NewsSource",
    "fetch_bytes",
    "parse_article_text",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/news-preprocessor/tests/test_article_body_parser.py -v`
Expected: 7 passed.

- [ ] **Step 6: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`

```bash
git add services/news-preprocessor/src/news_preprocessor/sources services/news-preprocessor/tests/test_article_body_parser.py
git commit -m "feat(preprocessor): add shared source types and the article body parser

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Hankyung economy adapter

**Files:**
- Create: `services/news-preprocessor/src/news_preprocessor/sources/publishers/__init__.py`
- Create: `services/news-preprocessor/src/news_preprocessor/sources/publishers/hankyung/__init__.py`, `rss.py`, `parser.py`
- Create: `services/news-preprocessor/tests/fixtures/hankyung_feed.xml`, `services/news-preprocessor/tests/fixtures/hankyung_article.html`
- Test: `services/news-preprocessor/tests/test_hankyung.py`

**Interfaces:**
- Consumes: everything Task 3 exports from `news_preprocessor.sources`.
- Produces: `news_preprocessor.sources.publishers.hankyung.HankyungEconomyRSS(fetch: Callable[[str], bytes] = fetch_bytes)` with class attributes `source = "hankyung_economy"`, `feed_url = "https://www.hankyung.com/feed/economy"`; implements `NewsSource`. `HankyungEconomyParser()` (body class `article-body`). `news_preprocessor.sources.publishers.SOURCES: tuple[NewsSource, ...]`.

Live feed facts this adapter encodes (2026-09-22): items have `title`, `link`, `author`, `pubDate` (`Tue, 22 Sep 2026 15:01:06 +0900`) and **no `guid`**, so `external_id` is the `link`. The body is `<div class="article-body">`.

- [ ] **Step 1: Add the synthetic fixtures**

They reproduce the live structure — an `article-body-wrap` outer div (must not match), `<img>`, `<br>`, `<br/>`, and page chrome after the body — with invented text. The third feed item has no `pubDate` and must be skipped; the second has whitespace around its `link` and must be stripped.

`services/news-preprocessor/tests/fixtures/hankyung_feed.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>한국경제 | 경제</title>
    <link>https://www.hankyung.com/economy</link>
    <item>
      <title><![CDATA[기준금리 동결…"연내 인하 가능성"]]></title>
      <link>https://www.hankyung.com/article/202609220001i</link>
      <author>김테스트</author>
      <pubDate>Tue, 22 Sep 2026 15:01:06 +0900</pubDate>
    </item>
    <item>
      <title>환율 1300원대 안착</title>
      <link> https://www.hankyung.com/article/202609220002i </link>
      <author>이테스트</author>
      <pubDate>Tue, 22 Sep 2026 14:30:00 +0900</pubDate>
    </item>
    <item>
      <title>발행 시각이 없는 기사</title>
      <link>https://www.hankyung.com/article/202609220003i</link>
      <author>박테스트</author>
    </item>
  </channel>
</rss>
```

`services/news-preprocessor/tests/fixtures/hankyung_article.html`:

```html
<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><title>기준금리 동결 | 한국경제</title><link rel="stylesheet" href="a.css"></head>
<body>
<header class="header"><img src="logo.png" alt="한국경제"><nav>경제 금융 부동산</nav></header>
<div class="article-body-wrap">
  <div class="article-body" id="articletxt" itemprop="articleBody">
    <figure class="article-figure"><div class="figure-img"><img src="photo.jpg" alt=""></div><figcaption class="figure-caption">한국은행 전경</figcaption></figure>
    한국은행이 기준금리를 연 2.50%로 동결했다.<br>
    시장은 예상된 결과라는 반응이다.<br/>
    <br>
    전문가들은 연내 인하 가능성을 점쳤다.<br>
    김테스트 기자 test@hankyung.com
  </div>
</div>
<div class="related-news"><h3>관련 기사</h3><ul><li>다른 기사</li></ul></div>
<footer class="footer"><p>사이트맵</p><p>Copyright 한국경제</p></footer>
</body>
</html>
```

- [ ] **Step 2: Write the failing tests**

`services/news-preprocessor/tests/test_hankyung.py`:

```python
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from news_preprocessor.sources import EmptyBodyError
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
KST = timezone(timedelta(hours=9))


def _fake_fetch(pages: dict[str, bytes]):
    def fetch(url: str) -> bytes:
        return pages[url]

    return fetch


def _source(article: bytes | None = None) -> HankyungEconomyRSS:
    pages = {HankyungEconomyRSS.feed_url: (FIXTURES / "hankyung_feed.xml").read_bytes()}
    if article is not None:
        pages["https://www.hankyung.com/article/202609220001i"] = article
    return HankyungEconomyRSS(fetch=_fake_fetch(pages))


def test_entries_parse_valid_items_and_skip_one_without_pubdate(caplog):
    entries = _source().entries()

    assert [entry.external_id for entry in entries] == [
        "https://www.hankyung.com/article/202609220001i",
        "https://www.hankyung.com/article/202609220002i",
    ]
    assert "skipping hankyung_economy feed item" in caplog.text


def test_entry_fields():
    first = _source().entries()[0]

    assert first.source == "hankyung_economy"
    assert first.url == first.external_id
    assert first.title == '기준금리 동결…"연내 인하 가능성"'
    assert first.published_at == datetime(2026, 9, 22, 15, 1, 6, tzinfo=KST)
    assert first.published_at.utcoffset() == timedelta(hours=9)
    assert first.raw_payload.startswith("<item>")


def test_article_extracts_the_body_only():
    source = _source((FIXTURES / "hankyung_article.html").read_bytes())
    entry = source.entries()[0]

    item = source.article(entry)

    assert item.body == (
        "한국은행 전경 한국은행이 기준금리를 연 2.50%로 동결했다. "
        "시장은 예상된 결과라는 반응이다. 전문가들은 연내 인하 가능성을 점쳤다. "
        "김테스트 기자 test@hankyung.com"
    )
    assert item.title == entry.title
    assert item.published_at == entry.published_at


def test_article_without_the_body_container_raises():
    source = _source(b"<html><body><div class='other'>no body</div></body></html>")
    entry = source.entries()[0]

    with pytest.raises(EmptyBodyError):
        source.article(entry)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest services/news-preprocessor/tests/test_hankyung.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'news_preprocessor.sources.publishers'`.

- [ ] **Step 4: Write the parser and adapter**

`services/news-preprocessor/src/news_preprocessor/sources/publishers/hankyung/parser.py`:

```python
"""Extracts the article body from Hankyung's page markup."""

from news_preprocessor.sources import ArticleBodyParser


class HankyungEconomyParser(ArticleBodyParser):
    def __init__(self) -> None:
        super().__init__("article-body")
```

`services/news-preprocessor/src/news_preprocessor/sources/publishers/hankyung/rss.py`:

```python
"""Hankyung's economy RSS feed.

Items carry `title`, `link`, `author` and an RFC 822 `pubDate` (`+0900`), and no `guid`.
"""

import logging
from collections.abc import Callable
from dataclasses import asdict
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from news_preprocessor.sources import (
    EmptyBodyError,
    FeedEntry,
    NewsItem,
    fetch_bytes,
    parse_article_text,
)
from news_preprocessor.sources.publishers.hankyung.parser import HankyungEconomyParser

logger = logging.getLogger(__name__)


class HankyungEconomyRSS:
    source = "hankyung_economy"
    feed_url = "https://www.hankyung.com/feed/economy"

    def __init__(self, fetch: Callable[[str], bytes] = fetch_bytes) -> None:
        self._fetch = fetch

    def entries(self) -> list[FeedEntry]:
        root = ElementTree.fromstring(self._fetch(self.feed_url))
        entries = []
        for item in root.findall("./channel/item"):
            try:
                entries.append(self._entry(item))
            except ValueError as error:
                logger.warning("skipping %s feed item: %s", self.source, error)
        return entries

    def article(self, entry: FeedEntry) -> NewsItem:
        body = parse_article_text(self._fetch(entry.url), HankyungEconomyParser())
        if not body:
            raise EmptyBodyError(entry.url)
        return NewsItem(**asdict(entry), body=body)

    def _entry(self, item: ElementTree.Element) -> FeedEntry:
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not (link and title and pub_date):
            raise ValueError(f"missing link, title or pubDate: {link or title!r}")
        published_at = parsedate_to_datetime(pub_date)
        if published_at.tzinfo is None:
            raise ValueError(f"pubDate has no timezone: {pub_date!r}")
        return FeedEntry(
            source=self.source,
            external_id=link,
            url=link,
            title=title,
            published_at=published_at,
            raw_payload=ElementTree.tostring(item, encoding="unicode"),
        )
```

`services/news-preprocessor/src/news_preprocessor/sources/publishers/hankyung/__init__.py`:

```python
"""Hankyung (한국경제) economy section."""

from news_preprocessor.sources.publishers.hankyung.parser import HankyungEconomyParser
from news_preprocessor.sources.publishers.hankyung.rss import HankyungEconomyRSS

__all__ = ["HankyungEconomyParser", "HankyungEconomyRSS"]
```

`services/news-preprocessor/src/news_preprocessor/sources/publishers/__init__.py` (Task 5 adds Maeil):

```python
"""One subpackage per news outlet. Add an outlet by adding a subpackage and an entry here."""

from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS

SOURCES: tuple[NewsSource, ...] = (HankyungEconomyRSS(),)

__all__ = ["SOURCES", "HankyungEconomyRSS"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/news-preprocessor/tests/test_hankyung.py -v`
Expected: 4 passed.

- [ ] **Step 6: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`

```bash
git add services/news-preprocessor/src/news_preprocessor/sources/publishers services/news-preprocessor/tests/fixtures/hankyung_feed.xml services/news-preprocessor/tests/fixtures/hankyung_article.html \
  services/news-preprocessor/tests/test_hankyung.py
git commit -m "feat(preprocessor): add the Hankyung economy adapter

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Maeil Business economy adapter

**Files:**
- Create: `services/news-preprocessor/src/news_preprocessor/sources/publishers/maeil/__init__.py`, `rss.py`, `parser.py`
- Modify: `services/news-preprocessor/src/news_preprocessor/sources/publishers/__init__.py`
- Create: `services/news-preprocessor/tests/fixtures/maeil_feed.xml`, `services/news-preprocessor/tests/fixtures/maeil_article.html`
- Test: `services/news-preprocessor/tests/test_maeil.py`

**Interfaces:**
- Consumes: everything Task 3 exports from `news_preprocessor.sources`.
- Produces: `news_preprocessor.sources.publishers.maeil.MaeilBusinessEconomyRSS(fetch=fetch_bytes)` with `source = "maeil_business_economy"`, `feed_url = "https://www.mk.co.kr/rss/30100041/"`; implements `NewsSource`. `MaeilBusinessEconomyParser()` (body class `news_cnt_detail_wrap`). `SOURCES` becomes `(HankyungEconomyRSS(), MaeilBusinessEconomyRSS())`.

Live feed facts (2026-09-22): items have `no`, `title`, `link`, `category`, `author`, `pubDate`, `description`, `media:content`, and **no `guid`**. `pubDate` is `Tue, 22 Sep 2026 14:37:38 +09:00`. **`email.utils.parsedate_to_datetime` accepts `+09:00` but returns a naive datetime** (verified on Python 3.13), which Postgres would store as UTC — nine hours off. The adapter rewrites the offset to `+0900` first, and rejects any naive result. The body is `<div class="news_cnt_detail_wrap">` inside `<section class="sec_body">`.

- [ ] **Step 1: Add the synthetic fixtures**

The first feed item carries every extra child element; the third has an unparseable `pubDate` and must be skipped. The article page has an image block, `<br>`s, a hidden `<input>`, and editor/most-read blocks after the body that must not be captured.

`services/news-preprocessor/tests/fixtures/maeil_feed.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>매일경제 : 경제</title>
    <link>https://www.mk.co.kr/news/economy/</link>
    <item>
      <no>10000001</no>
      <title><![CDATA[“집값 잡으려 어쩔수 없지만”…가계이자 부담 늘어]]></title>
      <link>https://www.mk.co.kr/news/economy/10000001</link>
      <category>경제</category>
      <author>매일경제</author>
      <pubDate>Tue, 22 Sep 2026 14:37:38 +09:00</pubDate>
      <description><![CDATA[요약문은 저장하지 않는다]]></description>
      <media:content url="https://wimg.mk.co.kr/test.jpg" medium="image"></media:content>
    </item>
    <item>
      <no>10000002</no>
      <title>수출 11개월 연속 증가</title>
      <link>https://www.mk.co.kr/news/economy/10000002</link>
      <category>경제</category>
      <author>매일경제</author>
      <pubDate>Tue, 22 Sep 2026 09:05:00 +09:00</pubDate>
      <description>요약</description>
    </item>
    <item>
      <no>10000003</no>
      <title>발행 시각이 깨진 기사</title>
      <link>https://www.mk.co.kr/news/economy/10000003</link>
      <pubDate>어제 오후</pubDate>
    </item>
  </channel>
</rss>
```

`services/news-preprocessor/tests/fixtures/maeil_article.html`:

```html
<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"><title>가계이자 부담 늘어 - 매일경제</title></head>
<body>
<section class="sec_head"><h2 class="news_ttl">가계이자 부담 늘어</h2></section>
<section class="sec_body">
<div class="news_cnt_detail_wrap" itemprop="articleBody">
<div class="thumb_area img"><figure><div class="thumb"><img src="https://wimg.mk.co.kr/test.jpg" alt="사진설명"></div><figcaption></figcaption></figure></div>
<p>한국은행이 기준금리를 0.25%포인트 올리면 가계 이자 부담이 3조원 늘어난다.</p>
<p>연체율은 15개월 뒤 정점에 이를 전망이다.<br><br>[최테스트 기자]</p>
<input type="hidden" name="article_id" value="10000001">
</div>
<div class="box_editor_wrap"><div class="editor_in">기자 소개</div></div>
</section>
<div class="view_mainnews"><div class="txt_area">많이 본 뉴스</div></div>
<footer class="sitemap_sec"><p>매일경제 사이트맵</p></footer>
</body>
</html>
```

- [ ] **Step 2: Write the failing tests**

`services/news-preprocessor/tests/test_maeil.py`:

```python
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from news_preprocessor.sources import EmptyBodyError
from news_preprocessor.sources.publishers.maeil import MaeilBusinessEconomyRSS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
KST = timezone(timedelta(hours=9))


def _fake_fetch(pages: dict[str, bytes]):
    def fetch(url: str) -> bytes:
        return pages[url]

    return fetch


def _source(article: bytes | None = None) -> MaeilBusinessEconomyRSS:
    pages = {MaeilBusinessEconomyRSS.feed_url: (FIXTURES / "maeil_feed.xml").read_bytes()}
    if article is not None:
        pages["https://www.mk.co.kr/news/economy/10000001"] = article
    return MaeilBusinessEconomyRSS(fetch=_fake_fetch(pages))


def test_entries_parse_valid_items_and_skip_one_with_a_broken_pubdate(caplog):
    entries = _source().entries()

    assert [entry.external_id for entry in entries] == [
        "https://www.mk.co.kr/news/economy/10000001",
        "https://www.mk.co.kr/news/economy/10000002",
    ]
    assert "skipping maeil_business_economy feed item" in caplog.text


def test_colon_offset_pubdate_is_timezone_aware():
    first = _source().entries()[0]

    assert first.published_at.tzinfo is not None
    assert first.published_at.utcoffset() == timedelta(hours=9)
    assert first.published_at == datetime(2026, 9, 22, 14, 37, 38, tzinfo=KST)


def test_extra_item_children_are_kept_only_in_the_raw_payload():
    first = _source().entries()[0]

    assert first.title == "“집값 잡으려 어쩔수 없지만”…가계이자 부담 늘어"
    assert first.url == first.external_id
    assert "<no>10000001</no>" in first.raw_payload
    assert "요약문은 저장하지 않는다" in first.raw_payload


def test_article_extracts_the_body_only():
    source = _source((FIXTURES / "maeil_article.html").read_bytes())

    item = source.article(source.entries()[0])

    assert item.body == (
        "한국은행이 기준금리를 0.25%포인트 올리면 가계 이자 부담이 3조원 늘어난다. "
        "연체율은 15개월 뒤 정점에 이를 전망이다.[최테스트 기자]"
    )
    assert item.source == "maeil_business_economy"


def test_article_without_the_body_container_raises():
    source = _source(b"<html><body><p>no body</p></body></html>")

    with pytest.raises(EmptyBodyError):
        source.article(source.entries()[0])
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest services/news-preprocessor/tests/test_maeil.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'news_preprocessor.sources.publishers.maeil'`.

- [ ] **Step 4: Write the parser and adapter**

`services/news-preprocessor/src/news_preprocessor/sources/publishers/maeil/parser.py`:

```python
"""Extracts the article body from Maeil Business's page markup."""

from news_preprocessor.sources import ArticleBodyParser


class MaeilBusinessEconomyParser(ArticleBodyParser):
    def __init__(self) -> None:
        super().__init__("news_cnt_detail_wrap")
```

`services/news-preprocessor/src/news_preprocessor/sources/publishers/maeil/rss.py`:

```python
"""Maeil Business's economy RSS feed.

Items carry `no`, `title`, `link`, `category`, `author`, `pubDate`, `description` and
`media:content`, and no `guid`. `pubDate` writes its offset as `+09:00`, which is not
RFC 822: `parsedate_to_datetime` accepts it but returns a naive datetime, so the offset
is rewritten to `+0900` first.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import asdict
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from news_preprocessor.sources import (
    EmptyBodyError,
    FeedEntry,
    NewsItem,
    fetch_bytes,
    parse_article_text,
)
from news_preprocessor.sources.publishers.maeil.parser import MaeilBusinessEconomyParser

logger = logging.getLogger(__name__)

_COLON_OFFSET = re.compile(r"([+-]\d{2}):(\d{2})$")


class MaeilBusinessEconomyRSS:
    source = "maeil_business_economy"
    feed_url = "https://www.mk.co.kr/rss/30100041/"

    def __init__(self, fetch: Callable[[str], bytes] = fetch_bytes) -> None:
        self._fetch = fetch

    def entries(self) -> list[FeedEntry]:
        root = ElementTree.fromstring(self._fetch(self.feed_url))
        entries = []
        for item in root.findall("./channel/item"):
            try:
                entries.append(self._entry(item))
            except ValueError as error:
                logger.warning("skipping %s feed item: %s", self.source, error)
        return entries

    def article(self, entry: FeedEntry) -> NewsItem:
        body = parse_article_text(self._fetch(entry.url), MaeilBusinessEconomyParser())
        if not body:
            raise EmptyBodyError(entry.url)
        return NewsItem(**asdict(entry), body=body)

    def _entry(self, item: ElementTree.Element) -> FeedEntry:
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not (link and title and pub_date):
            raise ValueError(f"missing link, title or pubDate: {link or title!r}")
        published_at = parsedate_to_datetime(_COLON_OFFSET.sub(r"\1\2", pub_date))
        if published_at.tzinfo is None:
            raise ValueError(f"pubDate has no timezone: {pub_date!r}")
        return FeedEntry(
            source=self.source,
            external_id=link,
            url=link,
            title=title,
            published_at=published_at,
            raw_payload=ElementTree.tostring(item, encoding="unicode"),
        )
```

`services/news-preprocessor/src/news_preprocessor/sources/publishers/maeil/__init__.py`:

```python
"""Maeil Business (매일경제) economy section."""

from news_preprocessor.sources.publishers.maeil.parser import MaeilBusinessEconomyParser
from news_preprocessor.sources.publishers.maeil.rss import MaeilBusinessEconomyRSS

__all__ = ["MaeilBusinessEconomyParser", "MaeilBusinessEconomyRSS"]
```

Replace `services/news-preprocessor/src/news_preprocessor/sources/publishers/__init__.py`. It lives here, not in `sources/__init__.py`: the publisher modules import the shared types from `news_preprocessor.sources`, so re-exporting `SOURCES` from there would be a circular import.

`services/news-preprocessor/src/news_preprocessor/sources/publishers/__init__.py`:

```python
"""One subpackage per news outlet. Add an outlet by adding a subpackage and an entry here."""

from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS
from news_preprocessor.sources.publishers.maeil import MaeilBusinessEconomyRSS

SOURCES: tuple[NewsSource, ...] = (HankyungEconomyRSS(), MaeilBusinessEconomyRSS())

__all__ = ["SOURCES", "HankyungEconomyRSS", "MaeilBusinessEconomyRSS"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest services/news-preprocessor -v`
Expected: all pass (Maeil 5, Hankyung 4, parser 7, settings and main unchanged).

- [ ] **Step 6: Check the timezone regression test actually guards the fix**

Temporarily change `_COLON_OFFSET.sub(r"\1\2", pub_date)` to `pub_date` in `maeil/rss.py` and run `uv run pytest services/news-preprocessor/tests/test_maeil.py -v`.
Expected: all 5 Maeil tests FAIL — both valid items are now rejected as naive, so every test that reads `entries()` breaks (verified). Revert the change and re-run: all pass.

- [ ] **Step 7: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`

```bash
git add services/news-preprocessor/src/news_preprocessor/sources/publishers services/news-preprocessor/tests/fixtures/maeil_feed.xml services/news-preprocessor/tests/fixtures/maeil_article.html \
  services/news-preprocessor/tests/test_maeil.py
git commit -m "feat(preprocessor): add the Maeil Business economy adapter

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Storage and the migration-drift check

**Files:**
- Modify: `services/news-preprocessor/pyproject.toml`, `uv.lock`, `docker/requirements/news-preprocessor.txt`
- Create: `services/news-preprocessor/src/news_preprocessor/storage.py`
- Test: `services/news-preprocessor/tests/test_storage.py`
- Modify: `infrastructure/postgres/tests/test_migrations.py`

**Interfaces:**
- Consumes: `NewsItem` (Task 3), `EMBEDDING_DIMENSIONS` (Task 2), the `articles` migration (Task 1), fixtures `pg_conn`, `pg_dsn`, `pg_engine` (Task 1).
- Produces (`news_preprocessor.storage`): `metadata: sa.MetaData`, `articles: sa.Table`, and
  - `known_external_ids(conn: sa.Connection, source: str, external_ids: list[str]) -> set[str]`
  - `insert_new(conn: sa.Connection, item: NewsItem) -> bool` — `True` if a row was added
  - `pending_embedding(conn: sa.Connection, limit: int) -> Sequence[sa.Row]` — rows with `.id`, `.title`, `.body`, `embedding IS NULL`, by `id`
  - `set_embedding(conn: sa.Connection, ids: Sequence[int], vectors: Sequence[list[float]]) -> None` — `ValueError` if lengths differ

Verified against pgvector 0.8.6 / `pgvector` 0.5.0 / SQLAlchemy 2.0.54 / psycopg 3.3.6: `pgvector.sqlalchemy.VECTOR` binds and returns plain `list[float]` with no `register_vector` hook; `INSERT … ON CONFLICT DO NOTHING` reports `rowcount == -1` either way, so `insert_new` detects insertion with `RETURNING id`.

- [ ] **Step 1: Declare the service's database dependencies**

In `services/news-preprocessor/pyproject.toml`:

```toml
dependencies = [
    "ktb-core",
    "pydantic-settings>=2.7",
    "sqlalchemy>=2.0.54",
    "psycopg[binary]>=3.3.6",
    "pgvector>=0.5.0",
]
```

Run `uv lock`, `uv sync --all-packages --locked --group migrations`, then the export loop from Global Constraints. Expected: `git status` shows `uv.lock` and `docker/requirements/news-preprocessor.txt` modified; the other two requirement files unchanged.

- [ ] **Step 2: Write the failing storage tests**

`services/news-preprocessor/tests/test_storage.py`:

```python
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from news_preprocessor.sources import NewsItem
from news_preprocessor.storage import (
    articles,
    insert_new,
    known_external_ids,
    pending_embedding,
    set_embedding,
)


@pytest.fixture
def conn(pg_conn):
    """An empty `articles`, inside a transaction that is rolled back after the test."""
    pg_conn.execute(sa.text("TRUNCATE articles"))
    return pg_conn


def _item(external_id: str, source: str = "hankyung_economy") -> NewsItem:
    return NewsItem(
        source=source,
        external_id=external_id,
        url=external_id,
        title=f"제목 {external_id}",
        published_at=datetime(2026, 9, 22, 6, 0, tzinfo=UTC),
        raw_payload="<item/>",
        body=f"본문 {external_id}",
    )


def _vector(first: float) -> list[float]:
    return [first] + [0.0] * 1999


def test_insert_new_adds_a_row_once(conn):
    assert insert_new(conn, _item("a")) is True
    assert insert_new(conn, _item("a")) is False

    count = conn.execute(sa.select(sa.func.count()).select_from(articles)).scalar()
    assert count == 1


def test_the_same_external_id_from_another_source_is_a_different_article(conn):
    assert insert_new(conn, _item("a", source="hankyung_economy")) is True
    assert insert_new(conn, _item("a", source="maeil_business_economy")) is True


def test_known_external_ids_is_scoped_to_the_source(conn):
    insert_new(conn, _item("a"))
    insert_new(conn, _item("b", source="maeil_business_economy"))

    assert known_external_ids(conn, "hankyung_economy", ["a", "b", "c"]) == {"a"}
    assert known_external_ids(conn, "hankyung_economy", []) == set()


def test_pending_embedding_returns_only_null_rows_in_id_order_up_to_the_limit(conn):
    for external_id in ("a", "b", "c"):
        insert_new(conn, _item(external_id))
    first_id = pending_embedding(conn, limit=10)[0].id
    set_embedding(conn, [first_id], [_vector(1.0)])

    rows = pending_embedding(conn, limit=1)

    assert [(row.title, row.body) for row in rows] == [("제목 b", "본문 b")]
    assert len(pending_embedding(conn, limit=10)) == 2


def test_set_embedding_writes_the_vector(conn):
    insert_new(conn, _item("a"))
    [row] = pending_embedding(conn, limit=10)

    set_embedding(conn, [row.id], [_vector(0.5)])

    stored = conn.execute(sa.select(articles.c.embedding)).scalar_one()
    assert len(stored) == 2000
    assert float(stored[0]) == 0.5


def test_set_embedding_rejects_mismatched_lengths(conn):
    with pytest.raises(ValueError):
        set_embedding(conn, [1, 2], [_vector(1.0)])
```

- [ ] **Step 3: Write the failing drift test**

Add these imports to `infrastructure/postgres/tests/test_migrations.py` (keep them sorted with the existing `alembic` imports):

```python
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
```

Append:

```python
def test_service_table_matches_the_migrated_schema(pg_dsn, pg_engine, monkeypatch):
    from news_preprocessor.storage import metadata

    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")

    def only_mirrored_tables(name, type_, parent_names):
        return name in metadata.tables if type_ == "table" else True

    with pg_engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "include_name": only_mirrored_tables}
        )
        assert compare_metadata(context, metadata) == []
```

`include_name` limits the comparison to tables the service mirrors; without it `alembic_version` reports as a spurious `remove_table`. Verified: this returns `[]` for a matching schema, and reports `modify_type` for `VECTOR(1999)` vs `vector(2000)` and `modify_nullable` for a nullability change.

- [ ] **Step 4: Run them to verify they fail**

Run: `uv run pytest services/news-preprocessor/tests/test_storage.py infrastructure -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'news_preprocessor.storage'`.

- [ ] **Step 5: Implement storage**

`services/news-preprocessor/src/news_preprocessor/storage.py`:

```python
"""Queries against `articles`. The schema is owned by the Alembic migrations in
infrastructure/postgres; this Table mirrors it for queries only and never issues DDL."""

from collections.abc import Sequence
from dataclasses import asdict

import sqlalchemy as sa
from ktb_core.embedding import EMBEDDING_DIMENSIONS
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import insert

from news_preprocessor.sources import NewsItem

metadata = sa.MetaData()

articles = sa.Table(
    "articles",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("external_id", sa.Text, nullable=False),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("raw_payload", sa.Text, nullable=False),
    sa.Column(
        "fetched_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("embedding", VECTOR(EMBEDDING_DIMENSIONS), nullable=True),
    sa.UniqueConstraint("source", "external_id", name="articles_source_external_id_key"),
    sa.Index(
        "articles_embedding_hnsw",
        "embedding",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    ),
    sa.Index("articles_published_at_idx", "published_at"),
)


def known_external_ids(conn: sa.Connection, source: str, external_ids: list[str]) -> set[str]:
    if not external_ids:
        return set()
    query = sa.select(articles.c.external_id).where(
        articles.c.source == source, articles.c.external_id.in_(external_ids)
    )
    return set(conn.execute(query).scalars())


def insert_new(conn: sa.Connection, item: NewsItem) -> bool:
    """Insert unless (source, external_id) already exists. Returns whether a row was added."""
    statement = (
        insert(articles)
        .values(**asdict(item))
        .on_conflict_do_nothing(constraint="articles_source_external_id_key")
        .returning(articles.c.id)
    )
    return conn.execute(statement).scalar_one_or_none() is not None


def pending_embedding(conn: sa.Connection, limit: int) -> Sequence[sa.Row]:
    query = (
        sa.select(articles.c.id, articles.c.title, articles.c.body)
        .where(articles.c.embedding.is_(None))
        .order_by(articles.c.id)
        .limit(limit)
    )
    return conn.execute(query).all()


def set_embedding(conn: sa.Connection, ids: Sequence[int], vectors: Sequence[list[float]]) -> None:
    for article_id, vector in zip(ids, vectors, strict=True):
        conn.execute(
            sa.update(articles).where(articles.c.id == article_id).values(embedding=vector)
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest services/news-preprocessor/tests/test_storage.py infrastructure -v`
Expected: 6 storage + 7 migration tests pass.

- [ ] **Step 7: Verify the service imports with only its declared dependencies**

Run: `uv run --isolated --package news-preprocessor --locked --no-dev python -c "import news_preprocessor.storage"`
Expected: exits 0.

- [ ] **Step 8: Lint, type-check, full suite, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`

```bash
git add services/news-preprocessor/pyproject.toml uv.lock docker/requirements services/news-preprocessor/src/news_preprocessor/storage.py \
  services/news-preprocessor/tests/test_storage.py infrastructure/postgres/tests/test_migrations.py
git commit -m "feat(preprocessor): add articles storage and the migration drift check

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Settings, the run flow, and the CI image check

**Files:**
- Modify: `services/news-preprocessor/src/news_preprocessor/settings.py`, `services/news-preprocessor/src/news_preprocessor/__main__.py`
- Modify (replace): `services/news-preprocessor/tests/test_settings.py`, `services/news-preprocessor/tests/test_main.py`
- Modify: `.github/workflows/ci.yaml` (`build-and-run-images`)

**Interfaces:**
- Consumes: `embed`, `EMBEDDING_BASE_URI_ENV` (Task 2); `NewsSource` (Task 3); `SOURCES`, `HankyungEconomyRSS`, `MaeilBusinessEconomyRSS` (Tasks 4–5); the four storage functions and `articles` (Task 6); `pg_engine` (Task 1).
- Produces: `Settings.embedding_base_uri: str` (from `KTB_EMBEDDING_BASE_URI`), `Settings.embed_batch_limit: int` (default 100, > 0); `news_preprocessor.__main__.run(engine: sa.Engine, sources: Sequence[NewsSource], embedder: Callable[[list[str]], list[list[float]]], embed_batch_limit: int) -> bool` (`False` if any feed, article or embedding call failed); `EMBED_BATCH_SIZE = 16`; `main()` exits 0 or 1.

- [ ] **Step 1: Write the failing settings tests**

`services/news-preprocessor/tests/test_settings.py`:

```python
import pytest
from news_preprocessor.settings import Settings
from pydantic import ValidationError

DSN = "postgresql+psycopg://ktb:ktb@localhost:5432/news"
EMBEDDING_BASE_URI = "http://embedder:8000/v1"


@pytest.fixture
def required_env(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", DSN)
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", EMBEDDING_BASE_URI)
    for name in ("NEWS_PREPROCESSOR_LOG_LEVEL", "NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT"):
        monkeypatch.delenv(name, raising=False)


def test_loads_from_the_environment(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT", "7")

    settings = Settings()

    assert settings.postgres_dsn == DSN
    assert settings.embedding_base_uri == EMBEDDING_BASE_URI
    assert settings.log_level == "DEBUG"
    assert settings.embed_batch_limit == 7


def test_defaults(required_env):
    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.embed_batch_limit == 100


def test_embedding_uri_is_the_shared_variable_not_a_prefixed_one(required_env, monkeypatch):
    monkeypatch.delenv("KTB_EMBEDDING_BASE_URI")
    monkeypatch.setenv("NEWS_PREPROCESSOR_EMBEDDING_BASE_URI", EMBEDDING_BASE_URI)

    with pytest.raises(ValidationError):
        Settings()


def test_missing_dsn_raises_at_construction(required_env, monkeypatch):
    monkeypatch.delenv("NEWS_PREPROCESSOR_POSTGRES_DSN")

    with pytest.raises(ValidationError):
        Settings()


def test_embed_batch_limit_must_be_positive(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT", "0")

    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Write the failing run-flow tests**

These drive the real adapters with the Task 4–5 fixtures and a fake embedder. `run()` commits per article, so the `engine` fixture truncates `articles` before and after instead of rolling back.

`services/news-preprocessor/tests/test_main.py`:

```python
import pathlib
from urllib.error import URLError

import pytest
import sqlalchemy as sa
from news_preprocessor import __main__ as entry
from news_preprocessor.__main__ import run
from news_preprocessor.sources.publishers import HankyungEconomyRSS, MaeilBusinessEconomyRSS
from news_preprocessor.storage import articles

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
VECTOR = [1.0] + [0.0] * 1999


class FakeWeb:
    """Serves each publisher's feed fixture, and its article fixture for any other URL."""

    def __init__(self, failing_feeds: tuple[str, ...] = ()):
        self.requested: list[str] = []
        self._failing_feeds = failing_feeds

    def fetcher(self, feed_url: str, feed: str, article: str):
        def fetch(url: str) -> bytes:
            self.requested.append(url)
            if url == feed_url:
                if url in self._failing_feeds:
                    raise URLError("feed unreachable")
                return (FIXTURES / feed).read_bytes()
            return (FIXTURES / article).read_bytes()

        return fetch

    def sources(self):
        return (
            HankyungEconomyRSS(
                fetch=self.fetcher(
                    HankyungEconomyRSS.feed_url, "hankyung_feed.xml", "hankyung_article.html"
                )
            ),
            MaeilBusinessEconomyRSS(
                fetch=self.fetcher(
                    MaeilBusinessEconomyRSS.feed_url, "maeil_feed.xml", "maeil_article.html"
                )
            ),
        )


def fake_embedder(texts: list[str]) -> list[list[float]]:
    return [VECTOR for _ in texts]


def failing_embedder(texts: list[str]) -> list[list[float]]:
    raise URLError("embedding host unreachable")


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE articles RESTART IDENTITY"))


@pytest.fixture
def engine(pg_engine):
    """`run()` commits, so these tests empty `articles` before and after instead of rolling back."""
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)


def _rows(engine):
    with engine.connect() as conn:
        query = sa.select(articles.c.source, articles.c.embedding).order_by(articles.c.id)
        return conn.execute(query).all()


def test_run_stores_and_embeds_articles_from_both_publishers(engine):
    assert run(engine, FakeWeb().sources(), fake_embedder, embed_batch_limit=100) is True

    rows = _rows(engine)
    assert [row.source for row in rows] == ["hankyung_economy"] * 2 + ["maeil_business_economy"] * 2
    assert all(len(row.embedding) == 2000 for row in rows)


def test_second_run_adds_nothing_and_fetches_only_the_feeds(engine):
    run(engine, FakeWeb().sources(), fake_embedder, embed_batch_limit=100)
    web = FakeWeb()

    assert run(engine, web.sources(), fake_embedder, embed_batch_limit=100) is True

    assert len(_rows(engine)) == 4
    assert web.requested == [HankyungEconomyRSS.feed_url, MaeilBusinessEconomyRSS.feed_url]


def test_a_failing_feed_does_not_stop_the_other_publisher(engine):
    web = FakeWeb(failing_feeds=(HankyungEconomyRSS.feed_url,))

    assert run(engine, web.sources(), fake_embedder, embed_batch_limit=100) is False

    assert [row.source for row in _rows(engine)] == ["maeil_business_economy"] * 2


def test_embedding_failure_keeps_articles_pending_until_a_later_run(engine):
    assert run(engine, FakeWeb().sources(), failing_embedder, embed_batch_limit=100) is False
    assert all(row.embedding is None for row in _rows(engine))

    assert run(engine, FakeWeb().sources(), fake_embedder, embed_batch_limit=100) is True
    assert all(row.embedding is not None for row in _rows(engine))


def test_embed_batch_limit_caps_one_run(engine):
    run(engine, FakeWeb().sources(), fake_embedder, embed_batch_limit=3)

    assert sum(row.embedding is not None for row in _rows(engine)) == 3


@pytest.mark.parametrize(("ok", "code"), [(True, 0), (False, 1)])
def test_main_exit_code_reflects_the_run(monkeypatch, ok, code):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "postgresql+psycopg://u@unused.invalid/db")
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", "http://embedder:8000/v1")
    monkeypatch.setattr(entry, "run", lambda *args: ok)

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == code
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest services/news-preprocessor/tests/test_settings.py services/news-preprocessor/tests/test_main.py -v`
Expected: FAIL — `ImportError: cannot import name 'run' from 'news_preprocessor.__main__'`, and settings tests fail with `AttributeError`/`ValidationError` on `embedding_base_uri`.

- [ ] **Step 4: Implement settings**

`services/news-preprocessor/src/news_preprocessor/settings.py`:

```python
"""Configuration for the news-preprocessor."""

from ktb_core.embedding import EMBEDDING_BASE_URI_ENV
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_PREPROCESSOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
    embedding_base_uri: str = Field(validation_alias=EMBEDDING_BASE_URI_ENV)
    embed_batch_limit: int = Field(default=100, gt=0)
```

- [ ] **Step 5: Implement the run flow**

Per the spec: a failing feed skips that source; a failing or empty article is logged and skipped; the first embedding failure stops embedding for this run (an unreachable host would fail every later batch too) and leaves the remaining rows `NULL` for the next run. Database errors are not caught.

`services/news-preprocessor/src/news_preprocessor/__main__.py`:

```python
"""Entry point for the news-preprocessor: one scrape-then-embed pass, then exit."""

import logging
import sys
from collections.abc import Callable, Sequence
from functools import partial
from itertools import batched

import sqlalchemy as sa
from ktb_core.embedding import embed
from ktb_core.logging import setup_logging

from news_preprocessor.settings import Settings
from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers import SOURCES
from news_preprocessor.storage import (
    insert_new,
    known_external_ids,
    pending_embedding,
    set_embedding,
)

EMBED_BATCH_SIZE = 16

logger = logging.getLogger(__name__)

Embedder = Callable[[list[str]], list[list[float]]]


def run(
    engine: sa.Engine,
    sources: Sequence[NewsSource],
    embedder: Embedder,
    embed_batch_limit: int,
) -> bool:
    """Scrape every source, then embed pending articles. Returns False if anything failed."""
    ok = True
    for source in sources:
        if not _scrape(engine, source):
            ok = False
    if not _embed_pending(engine, embedder, embed_batch_limit):
        ok = False
    return ok


def _scrape(engine: sa.Engine, source: NewsSource) -> bool:
    try:
        entries = source.entries()
    except Exception:
        logger.exception("feed failed for %s", source.source)
        return False

    with engine.connect() as conn:
        known = known_external_ids(conn, source.source, [entry.external_id for entry in entries])

    ok = True
    added = 0
    for entry in entries:
        if entry.external_id in known:
            continue
        try:
            item = source.article(entry)
        except Exception:
            logger.exception("article failed for %s: %s", source.source, entry.url)
            ok = False
            continue
        with engine.begin() as conn:
            added += insert_new(conn, item)
    logger.info("%s: %d feed entries, %d new articles", source.source, len(entries), added)
    return ok


def _embed_pending(engine: sa.Engine, embedder: Embedder, limit: int) -> bool:
    with engine.connect() as conn:
        rows = pending_embedding(conn, limit)

    embedded = 0
    for batch in batched(rows, EMBED_BATCH_SIZE):
        try:
            vectors = embedder([f"{row.title}\n\n{row.body}" for row in batch])
        except Exception:
            logger.exception("embedding failed; %d articles stay pending", len(rows) - embedded)
            return False
        with engine.begin() as conn:
            set_embedding(conn, [row.id for row in batch], vectors)
        embedded += len(batch)
    logger.info("embedded %d articles", embedded)
    return True


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-preprocessor started")
    engine = sa.create_engine(settings.postgres_dsn)
    try:
        ok = run(
            engine,
            SOURCES,
            partial(embed, base_uri=settings.embedding_base_uri),
            settings.embed_batch_limit,
        )
    finally:
        engine.dispose()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -v`
Expected: everything passes (68 with `KTB_TEST_POSTGRES_DSN` set).

Run: `env -u KTB_TEST_POSTGRES_DSN uv run pytest`
Expected: 54 passed, 14 skipped.

- [ ] **Step 7: Replace the preprocessor's image smoke step in CI**

The image now does real work: run under `--network none` it fails its first feed request and exits 1 (verified). In `build-and-run-images`, replace the step `One-shot services exit 0 with networking disabled` with two steps — the portfolio-builder part is unchanged:

```yaml
      - name: Preprocessor image carries every runtime dependency
        run: |
          docker run --rm --network none --entrypoint python \
            ktb-news-preprocessor -c "import news_preprocessor.__main__"
      - name: Portfolio builder exits 0 with networking disabled
        run: |
          set -euo pipefail
          docker run --rm --network none \
            -e PORTFOLIO_BUILDER_POSTGRES_DSN="$NEVER_DIALED_POSTGRES_DSN" \
            -e PORTFOLIO_BUILDER_QUESTDB_DSN="$NEVER_DIALED_QUESTDB_DSN" \
            -e PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL="$NEVER_DIALED_CLUSTERER_URL" \
            ktb-portfolio-builder
```

Verify locally:

```bash
docker build -f docker/news-preprocessor.Dockerfile -t ktb-news-preprocessor .
docker run --rm --network none --entrypoint python ktb-news-preprocessor -c "import news_preprocessor.__main__"
```

Expected: exits 0.

- [ ] **Step 8: Run the CI isolation loop locally**

```bash
for pkg in news-preprocessor news-clusterer portfolio-builder; do
  uv run --isolated --package "$pkg" --locked --no-dev python -c "import ${pkg//-/_}.__main__"
done
```

Expected: all three exit 0.

- [ ] **Step 9: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run ty check`

```bash
git add services/news-preprocessor/src/news_preprocessor/settings.py services/news-preprocessor/src/news_preprocessor/__main__.py services/news-preprocessor/tests/test_settings.py \
  services/news-preprocessor/tests/test_main.py .github/workflows/ci.yaml
git commit -m "feat(preprocessor): scrape then embed in one cron pass

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: End-to-end verification against the live sites and vLLM

No code changes. This is the spec's §10 acceptance run. Everything here was performed once while writing this plan (2026-09-22) and passed; the numbers below are what to expect.

- [ ] **Step 1: Make sure vLLM is serving embeddings**

The MLX build is detected as a generative model; without these flags vLLM exposes no `/v1/embeddings` (observed twice). The operator starts it with:

```bash
vllm serve mlx-community/Qwen3-Embedding-4B-4bit-DWQ \
  --runner pooling --convert embed \
  --max-model-len 16384 \
  --hf-overrides '{"is_matryoshka": true}'
```

Check:

```bash
curl -s http://100.77.120.106:8000/v1/models
```

Expected: `"id":"mlx-community/Qwen3-Embedding-4B-4bit-DWQ"` and `"max_model_len":16384`. Then:

```bash
curl -s http://100.77.120.106:8000/v1/embeddings -H 'content-type: application/json' \
  -d '{"model":"mlx-community/Qwen3-Embedding-4B-4bit-DWQ","input":["테스트"],"truncate_prompt_tokens":16384}' | head -c 80
```

Expected: a JSON body starting `{"id":"embd-`. A `{"detail":"Not Found"}` means the server was started without `--runner pooling --convert embed`.

- [ ] **Step 2: Migrate a fresh database (acceptance 1)**

```bash
docker compose -f compose.dev.yaml exec postgres createdb -U ktb news_e2e
export KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_e2e
uv run alembic upgrade head
uv run alembic downgrade base && uv run alembic upgrade head
```

Expected: all three commands exit 0.

- [ ] **Step 3: First run (acceptance 5)**

```bash
export NEWS_PREPROCESSOR_POSTGRES_DSN=$KTB_POSTGRES_DSN
export KTB_EMBEDDING_BASE_URI=http://100.77.120.106:8000/v1
uv run news-preprocessor; echo "exit=$?"
docker compose -f compose.dev.yaml exec postgres psql -U ktb -d news_e2e -c \
  "SELECT source, count(*), count(embedding), min(vector_dims(embedding)), max(vector_dims(embedding)) FROM articles GROUP BY source"
```

Expected: `exit=0`; about 50 rows per source, all embedded, dimensions 2000/2000. Observed 2026-09-22: 50 + 50 articles, scraping 16 s, **embedding 4 min 21 s** (~37 s per batch of 16 — much slower than the 1.3 s measured for synthetic batches; well inside the 120 s per-request timeout, but watch it).

- [ ] **Step 4: Immediate rerun (acceptance 6)**

```bash
uv run news-preprocessor; echo "exit=$?"
```

Expected: `exit=0`; logs show `50 feed entries, 0 new articles` for each source (or a handful of new ones if the feeds moved) and the row count grows only by those.

- [ ] **Step 5: Embedding outage and recovery (acceptance 7)**

```bash
docker compose -f compose.dev.yaml exec postgres psql -U ktb -d news_e2e -c \
  "UPDATE articles SET embedding = NULL WHERE id IN (SELECT id FROM articles ORDER BY id LIMIT 3)"
KTB_EMBEDDING_BASE_URI=http://127.0.0.1:9/v1 uv run news-preprocessor; echo "exit=$?"
uv run news-preprocessor; echo "exit=$?"
docker compose -f compose.dev.yaml exec postgres psql -U ktb -d news_e2e -tAc \
  "SELECT count(*) FROM articles WHERE embedding IS NULL"
```

Expected: first run `exit=1` with `embedding failed; 3 articles stay pending`; second run `exit=0` with `embedded 3 articles`; final count `0`.

- [ ] **Step 6: Clean up**

```bash
docker compose -f compose.dev.yaml exec postgres dropdb -U ktb news_e2e
```

---

## Known limitations (accepted, not tasks)

- **Maeil image-zoom label:** the zoom button's screen-reader text `사진 확대` inside `news_cnt_detail_wrap` is captured into bodies of articles that have photos; a photo-only article's body is just `사진 확대`. It is short, non-empty (so not an `EmptyBodyError`), and the title carries the meaning. Filter it in `MaeilBusinessEconomyParser` if clustering quality shows it matters.
- **Feed entries skipped for missing fields or naive dates** are logged as warnings but do not make the run exit 1 (spec §3.2).
- **Embedding latency** on real articles was ~37 s per batch of 16 (see Task 8 Step 3). If it approaches the 120 s timeout, lower `EMBED_BATCH_SIZE` before raising the timeout.

## Final verification

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run ty check` — clean.
- [ ] `uv run pytest` with `KTB_TEST_POSTGRES_DSN` set — all pass; without it — only database tests skip.
- [ ] The CI isolation loop and the preprocessor image import check pass locally.
- [ ] Task 8 Steps 3–5 behave as described.
- [ ] Every CI job passes on the PR (`python-314-compatibility` may fail).
