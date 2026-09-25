# news-graph-builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cron service `news-graph-builder` that turns each changed news cluster into a title, a summary and a knowledge graph with one LLM call, resolving KOSPI companies to shared nodes; move summarization out of `news-clusterer`.

**Architecture:** `news-clusterer` keeps writing `clusters` / `article_clusters`. `news-graph-builder` syncs KOSPI companies (Kiwoom `ka10099` joined with DART `corp_codes`), then, for every cluster whose `updated_at` moved past its stored summary, calls a vLLM OpenAI-compatible endpoint once and writes `cluster_summaries`, `entities`, `cluster_entities` and `relations` in one guarded transaction. Everything lives in PostgreSQL; services talk only through the database.

**Tech Stack:** Python 3.13 (CI also 3.14), uv workspace, SQLAlchemy 2 Core + psycopg 3, Alembic, httpx, pydantic-settings, opendartreader 0.3.3, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-news-graph-builder-design.md`

## Global Constraints

- Run every command from the worktree root. `uv sync --all-packages --group migrations` once before starting.
- Python `>=3.13,<3.15`; every new `pyproject.toml` uses `requires-python = ">=3.13,<3.15"`.
- **Tests never touch the `news` database.** The fixtures `TRUNCATE` tables. Create and migrate a test database once, and pass it to every pytest run:
  ```bash
  docker compose -f compose.dev.yaml exec postgres createdb -U ktb news_test
  KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run alembic upgrade head
  ```
  Every `pytest` command below is run as `KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run pytest ...` (written `PYTEST ...` below). After Task 1 adds migration `0003`, re-run the `alembic upgrade head` line above.
- **Secrets:** never `cat`, print, log or commit the Kiwoom key files (`~/.config/ktb4-ai/kiwoom/`) or the DART key (`OPENDART_API_KEY` in the main checkout's `.env`). Tests use fake values only. No step in this plan needs a real key.
- Code style (`AGENTS.md`): no comments or docstrings that restate names, comments only for a non-obvious *why*; one top-level function per module; no thin wrappers or single-caller helpers; configuration via environment variables with defaults except truly required values.
- After changing any member's dependencies: `uv lock`, then exactly
  `uv export --package <svc> --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/<svc>.txt`.
- Keep infrastructure addresses (IPs, hostnames) out of every committed file.
- Before every commit run `uv run ruff check --fix . && uv run ruff format .`; the code blocks below may need import re-ordering or line wrapping.
- Commit messages use `feat` / `fix` / `refactor` / `chore` / `docs` prefixes and end with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- This branch is stacked on `feat/10/news-clusterer` (#29).

## File Structure

```
infrastructure/postgres/migrations/versions/0003_create_graph.py   Task 1  schema
infrastructure/postgres/tests/test_migrations.py                   Task 1, 4
services/news-clusterer/...                                        Task 1  summaries removed
services/news-graph-builder/
  pyproject.toml                                                   Task 2
  src/news_graph_builder/
    __init__.py                                                    Task 2
    settings.py        Settings                                    Task 2
    normalize.py       normalize()                                 Task 3
    storage.py         tables, reads, company_entity_id,           Task 4, 7, 8
                       lock_cluster, write_graph
    extract.py         extract(), Entity, Relation, Extraction     Task 5
    kiwoom.py          fetch_kospi()                               Task 6
    dart.py            fetch_corp_codes(), DartCompany             Task 6
    sync_companies.py  sync_companies()                            Task 7
    resolve.py         resolve()                                   Task 8
    __main__.py        main()                                      Task 9
  tests/conftest.py, test_*.py                                     Tasks 2–9
docker/news-graph-builder.Dockerfile, docker/requirements/...      Task 2
compose.dev.yaml, tach.toml, .github/workflows/ci-*.yaml           Task 1, 2
AGENTS.md                                                          Task 10
```

Import graph (no cycles): `normalize` ← `storage` ← `resolve`, `sync_companies`; `extract` ← `storage`, `resolve`; `dart` ← `sync_companies`; everything ← `__main__`.

---

### Task 1: Move summaries out of `clusters` and add the graph schema

Migration `0003` and the news-clusterer trim land together: the migration drops the summary columns that the clusterer's storage metadata still declares, and `test_service_tables_match_the_migrated_schema` compares the two.

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0003_create_graph.py`
- Modify: `infrastructure/postgres/tests/test_migrations.py`
- Modify: `services/news-clusterer/src/news_clusterer/storage.py`
- Modify: `services/news-clusterer/src/news_clusterer/__main__.py`
- Modify: `services/news-clusterer/src/news_clusterer/settings.py`
- Delete: `services/news-clusterer/src/news_clusterer/summarize.py`, `services/news-clusterer/tests/test_summarize.py`
- Modify: `services/news-clusterer/tests/test_main.py`, `test_storage.py`, `test_settings.py`
- Modify: `services/news-clusterer/pyproject.toml`, `compose.dev.yaml`, `uv.lock`, `docker/requirements/news-clusterer.txt`
- Modify: `docs/superpowers/specs/2026-09-23-news-clusterer-design.md`

**Interfaces:**
- Produces tables: `clusters(id, updated_at)`, `companies`, `company_aliases`, `entities`, `cluster_summaries`, `cluster_entities`, `relations` exactly as in Step 3. Index and constraint names there are what Task 4's metadata must repeat.

- [ ] **Step 1: Write the failing migration tests**

Append to `infrastructure/postgres/tests/test_migrations.py`:

```python
def _columns(conn, table: str) -> set[str]:
    return set(
        conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        ).scalars()
    )


def test_summaries_live_outside_clusters(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)

    command.upgrade(_alembic_config(), "head")

    with pg_engine.connect() as conn:
        assert _columns(conn, "clusters") == {"id", "updated_at"}
        assert _columns(conn, "cluster_summaries") == {
            "cluster_id",
            "title",
            "summary",
            "cluster_updated_at",
            "summarized_at",
        }


def test_downgrade_to_0002_copies_summaries_back(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()
    command.upgrade(config, "head")
    with pg_engine.begin() as conn:
        cluster_id = conn.execute(
            sa.text("INSERT INTO clusters DEFAULT VALUES RETURNING id")
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO cluster_summaries (cluster_id, title, summary, cluster_updated_at)"
                " VALUES (:id, '제목', '요약', now())"
            ),
            {"id": cluster_id},
        )

    try:
        command.downgrade(config, "0002")
        with pg_engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT title, summary, summarized_at FROM clusters WHERE id = :id"),
                {"id": cluster_id},
            ).one()
        assert (row.title, row.summary) == ("제목", "요약")
        assert row.summarized_at is not None
    finally:
        command.upgrade(config, "head")
        with pg_engine.begin() as conn:
            conn.execute(sa.text("TRUNCATE clusters CASCADE"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTEST infrastructure/postgres/tests/test_migrations.py -k "summaries" -v`
Expected: FAIL — `clusters` still has `title`, `summary`, `summarized_at`; `cluster_summaries` does not exist.

- [ ] **Step 3: Write migration `0003`**

Create `infrastructure/postgres/migrations/versions/0003_create_graph.py`:

```python
"""move cluster summaries out of clusters and create the knowledge graph

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("corp_code", sa.Text, primary_key=True),
        sa.Column("stock_code", sa.Text, nullable=False),
        sa.Column("corp_name", sa.Text, nullable=False),
        sa.Column("corp_eng_name", sa.Text, nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "company_aliases",
        sa.Column("alias", sa.Text, primary_key=True),
        sa.Column("corp_code", sa.Text, sa.ForeignKey("companies.corp_code"), nullable=False),
    )
    op.create_table(
        "entities",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("raw_name", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("corp_code", sa.Text, sa.ForeignKey("companies.corp_code"), nullable=True),
    )
    op.create_index(
        "entities_corp_code_key",
        "entities",
        ["corp_code"],
        unique=True,
        postgresql_where=sa.text("corp_code IS NOT NULL"),
    )
    op.create_index(
        "entities_name_type_key",
        "entities",
        ["name", "type"],
        unique=True,
        postgresql_where=sa.text("corp_code IS NULL"),
    )
    op.create_table(
        "cluster_summaries",
        sa.Column(
            "cluster_id",
            sa.BigInteger,
            sa.ForeignKey("clusters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("cluster_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "summarized_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "cluster_entities",
        sa.Column(
            "cluster_id",
            sa.BigInteger,
            sa.ForeignKey("clusters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), primary_key=True),
    )
    op.create_index("cluster_entities_entity_id_idx", "cluster_entities", ["entity_id"])
    op.create_table(
        "relations",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "cluster_id",
            sa.BigInteger,
            sa.ForeignKey("clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), nullable=False
        ),
        sa.Column(
            "target_entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), nullable=False
        ),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
    )
    op.create_index("relations_cluster_id_idx", "relations", ["cluster_id"])
    op.create_index("relations_source_entity_id_idx", "relations", ["source_entity_id"])
    op.create_index("relations_target_entity_id_idx", "relations", ["target_entity_id"])
    op.drop_column("clusters", "title")
    op.drop_column("clusters", "summary")
    op.drop_column("clusters", "summarized_at")


def downgrade() -> None:
    op.add_column("clusters", sa.Column("title", sa.Text, nullable=True))
    op.add_column("clusters", sa.Column("summary", sa.Text, nullable=True))
    op.add_column(
        "clusters", sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE clusters SET title = s.title, summary = s.summary,"
        " summarized_at = s.summarized_at"
        " FROM cluster_summaries s WHERE s.cluster_id = clusters.id"
    )
    op.drop_table("relations")
    op.drop_table("cluster_entities")
    op.drop_table("cluster_summaries")
    op.drop_table("entities")
    op.drop_table("company_aliases")
    op.drop_table("companies")
```

- [ ] **Step 4: Trim the clusterer's storage**

In `services/news-clusterer/src/news_clusterer/storage.py`:

1. Replace the `articles` table with only the columns `load_embeddings` reads:
   ```python
   articles = sa.Table(
       "articles",
       metadata,
       sa.Column("id", sa.BigInteger, primary_key=True),
       sa.Column("embedding", VECTOR(EMBEDDING_DIMENSIONS), nullable=True),
   )
   ```
2. Replace the `clusters` table with:
   ```python
   clusters = sa.Table(
       "clusters",
       metadata,
       sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
       sa.Column(
           "updated_at",
           sa.DateTime(timezone=True),
           nullable=False,
           server_default=sa.text("now()"),
       ),
   )
   ```
3. Delete `clusters_needing_summary`, `cluster_articles` and `set_summary` (the last three functions in the file). `load_embeddings`, `load_assignment`, `_by_article` and `write_clusters` stay unchanged.

- [ ] **Step 5: Trim the clusterer's entry point**

Replace `services/news-clusterer/src/news_clusterer/__main__.py` with:

```python
import logging
import resource
import time
from contextlib import ExitStack

import sqlalchemy as sa
from ktb_core.logging import setup_logging

from news_clusterer.dbscan import NOISE, dbscan
from news_clusterer.match import match
from news_clusterer.settings import Settings
from news_clusterer.storage import load_assignment, load_embeddings, write_clusters

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-clusterer started")
    with ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
        started = time.perf_counter()
        with engine.connect() as conn:
            article_ids, vectors = load_embeddings(conn)
        loaded = time.perf_counter()
        labels = dbscan(vectors, settings.eps, settings.min_samples)
        clustered = time.perf_counter()

        new: dict[int, set[int]] = {}
        for article_id, label in zip(article_ids, labels.tolist(), strict=True):
            if label != NOISE:
                new.setdefault(label, set()).add(article_id)
        clustered_count = sum(len(members) for members in new.values())
        # Keep this key=value format stable: it decides when to leave full-recompute DBSCAN.
        logger.info(
            "clustering cost: articles=%d clusters=%d noise=%d load_seconds=%.2f"
            " dbscan_seconds=%.2f peak_rss_mib=%d",
            len(article_ids),
            len(new),
            len(article_ids) - clustered_count,
            loaded - started,
            clustered - loaded,
            # Linux reports ru_maxrss in KiB.
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        )

        with engine.begin() as conn:
            old = load_assignment(conn)
            matches, unmatched = match(new, old)
            write_clusters(conn, new, old, matches, unmatched)


if __name__ == "__main__":
    main()
```

Failures now propagate as exceptions, which exit non-zero on their own; there is no summary failure left to count.

- [ ] **Step 6: Trim the clusterer's settings**

Replace the body of `Settings` in `services/news-clusterer/src/news_clusterer/settings.py` so only these fields remain:

```python
    log_level: str = "INFO"
    postgres_dsn: str
    # Cosine distance lies in [0, 2].
    eps: float = Field(default=0.2, gt=0, le=2)
    min_samples: int = Field(default=3, gt=0)
```

- [ ] **Step 7: Update the clusterer's tests**

Delete `services/news-clusterer/src/news_clusterer/summarize.py` and `services/news-clusterer/tests/test_summarize.py`.

In `services/news-clusterer/tests/test_settings.py`:
- `REQUIRED = {"NEWS_CLUSTERER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/news"}`
- `test_defaults` becomes:
  ```python
  def test_defaults(required_env):
      settings = Settings()

      assert settings.eps == 0.2
      assert settings.min_samples == 3
  ```
- Remove the `("NEWS_CLUSTERER_SUMMARY_MAX_CHARS", "0")` row from `test_out_of_range_values_raise`.

Replace `services/news-clusterer/tests/test_main.py` with:

```python
import logging

import pytest
import sqlalchemy as sa
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
from news_clusterer import __main__ as entry


def basis(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[index] = 1.0
    return vector


@pytest.fixture
def env(monkeypatch, pg_dsn):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", pg_dsn)
    # setup_logging replaces the root handlers, which would detach caplog.
    monkeypatch.setattr(entry, "setup_logging", lambda level: None)


@pytest.fixture
def two_events_and_noise(engine, article):
    with engine.begin() as conn:
        for _ in range(3):
            article(conn, basis(0))
        for _ in range(3):
            article(conn, basis(1))
        article(conn, basis(2))


def cluster_rows(engine):
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT id, updated_at FROM clusters ORDER BY id")).all()


def test_clusters_and_logs_the_cost(env, engine, two_events_and_noise, caplog):
    caplog.set_level(logging.INFO)

    entry.main()

    assert len(cluster_rows(engine)) == 2
    cost = [r.getMessage() for r in caplog.records if r.getMessage().startswith("clustering cost:")]
    assert len(cost) == 1
    assert "articles=7 clusters=2 noise=1 " in cost[0]
    assert "dbscan_seconds=" in cost[0] and "peak_rss_mib=" in cost[0]


def test_a_second_run_without_new_articles_changes_nothing(env, engine, two_events_and_noise):
    entry.main()
    before = cluster_rows(engine)

    entry.main()

    assert cluster_rows(engine) == before
```

In `services/news-clusterer/tests/test_storage.py`:
- Imports become:
  ```python
  from datetime import datetime

  import numpy as np
  from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
  from news_clusterer.match import match
  from news_clusterer.storage import load_assignment, load_embeddings, write_clusters
  ```
- Add after `apply`:
  ```python
  def updated_at(engine) -> dict[int, datetime]:
      with engine.connect() as conn:
          return dict(conn.exec_driver_sql("SELECT id, updated_at FROM clusters").all())
  ```
- Replace `test_first_write_creates_clusters_that_need_summaries`, `test_rewrite_keeps_ids_moves_members_and_drops_noise` and `test_an_unchanged_cluster_stays_summarized` with:
  ```python
  def test_first_write_creates_clusters(engine, article):
      with engine.begin() as conn:
          a, b, c = (article(conn) for _ in range(3))

      apply(engine, {0: {a, b}, 1: {c}})

      with engine.connect() as conn:
          assignment = load_assignment(conn)
      assert sorted(assignment.values(), key=min) == [{a, b}, {c}]
      assert set(updated_at(engine)) == set(assignment)


  def test_rewrite_keeps_ids_moves_members_and_drops_noise(engine, article):
      with engine.begin() as conn:
          a, b, c, d, e = (article(conn) for _ in range(5))
      apply(engine, {0: {a, b}, 1: {c, d}})
      with engine.connect() as conn:
          before = load_assignment(conn)
      kept = next(cluster_id for cluster_id, members in before.items() if a in members)
      stamps = updated_at(engine)

      # c and d become noise, e joins a's cluster.
      apply(engine, {0: {a, b, e}})

      with engine.connect() as conn:
          assert load_assignment(conn) == {kept: {a, b, e}}
      after = updated_at(engine)
      assert list(after) == [kept]
      assert after[kept] > stamps[kept]


  def test_an_unchanged_cluster_keeps_its_updated_at(engine, article):
      with engine.begin() as conn:
          a, b = article(conn), article(conn)
      apply(engine, {0: {a, b}})
      stamps = updated_at(engine)

      apply(engine, {5: {a, b}})

      assert updated_at(engine) == stamps
  ```
- Delete `test_cluster_articles_are_newest_first` and `test_set_summary_stores_title_and_summary` (Task 4 moves the first to news-graph-builder).

- [ ] **Step 8: Update packaging, compose and the clusterer spec**

- `services/news-clusterer/pyproject.toml`: remove `"httpx>=0.28",` and set `description = "Cron-triggered news clustering"`.
- `compose.dev.yaml`: in the `news-clusterer` service remove the two lines `- NEWS_CLUSTERER_LLM_BASE_URI` and `- NEWS_CLUSTERER_LLM_MODEL`.
- `docs/superpowers/specs/2026-09-23-news-clusterer-design.md`: directly under the `## 6. Summaries` heading insert:
  ```markdown
  > **Superseded (2026-09-25):** summaries moved to news-graph-builder and to the
  > `cluster_summaries` table; see `2026-09-24-news-graph-builder-design.md`.
  ```
- Run:
  ```bash
  uv lock
  uv export --package news-clusterer --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/news-clusterer.txt
  uv sync --all-packages --group migrations
  KTB_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run alembic upgrade head
  ```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `PYTEST infrastructure services/news-clusterer -v`
Expected: all PASS, including both new migration tests and `test_service_tables_match_the_migrated_schema[news_clusterer.storage-...]`.

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add infrastructure services/news-clusterer compose.dev.yaml uv.lock docker/requirements/news-clusterer.txt docs/superpowers/specs/2026-09-23-news-clusterer-design.md
git commit -m "refactor(clusterer): move summaries to cluster_summaries and add the graph schema

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Scaffold the news-graph-builder member

**Files:**
- Create: `services/news-graph-builder/pyproject.toml`
- Create: `services/news-graph-builder/src/news_graph_builder/__init__.py` (empty)
- Create: `services/news-graph-builder/src/news_graph_builder/settings.py`
- Create: `services/news-graph-builder/tests/test_settings.py`
- Create: `docker/news-graph-builder.Dockerfile`, `docker/requirements/news-graph-builder.txt`
- Modify: `tach.toml`, `compose.dev.yaml`, `.github/workflows/ci-dev.yaml`, `.github/workflows/ci-main.yaml`, `uv.lock`

**Interfaces:**
- Produces: `news_graph_builder.settings.Settings` with fields `log_level: str`, `postgres_dsn: str`, `llm_base_uri: str`, `llm_model: str`, `kiwoom_app_key: SecretStr`, `kiwoom_secret_key: SecretStr`, `dart_api_key: SecretStr`, `kiwoom_base_uri: str`, `summary_max_chars: int`, `llm_timeout: float`, `max_entities: int`, `max_relations: int`.

- [ ] **Step 1: Create the member's `pyproject.toml`**

```toml
[project]
name = "news-graph-builder"
version = "0.1.0"
description = "Cron-triggered cluster summaries and knowledge graph"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "pydantic-settings>=2.7",
    "sqlalchemy>=2.0.54",
    "psycopg[binary]>=3.3.6",
    "httpx>=0.28",
    "opendartreader>=0.3.3",
]

[project.scripts]
news-graph-builder = "news_graph_builder.__main__:main"

[tool.uv.sources]
ktb-core = { workspace = true }

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"
```

Create the empty `src/news_graph_builder/__init__.py`, then run `uv lock && uv sync --all-packages --group migrations`.

- [ ] **Step 2: Write the failing settings tests**

`services/news-graph-builder/tests/test_settings.py`:

```python
import pytest
from news_graph_builder.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "NEWS_GRAPH_BUILDER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/news",
    "NEWS_GRAPH_BUILDER_LLM_BASE_URI": "http://llm.test/v1",
    "NEWS_GRAPH_BUILDER_LLM_MODEL": "test-model",
    "NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY": "app-key",
    "NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY": "secret-key",
    "NEWS_GRAPH_BUILDER_DART_API_KEY": "dart-key",
}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = Settings()

    assert settings.kiwoom_base_uri == "https://api.kiwoom.com"
    assert settings.summary_max_chars == 24000
    assert settings.llm_timeout == 120
    assert settings.max_entities == 30
    assert settings.max_relations == 50


def test_keys_are_hidden_from_repr(required_env):
    settings = Settings()

    assert settings.dart_api_key.get_secret_value() == "dart-key"
    assert "dart-key" not in repr(settings)
    assert "secret-key" not in repr(settings)


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "name",
    [
        "NEWS_GRAPH_BUILDER_SUMMARY_MAX_CHARS",
        "NEWS_GRAPH_BUILDER_LLM_TIMEOUT",
        "NEWS_GRAPH_BUILDER_MAX_ENTITIES",
        "NEWS_GRAPH_BUILDER_MAX_RELATIONS",
    ],
)
def test_non_positive_values_raise(required_env, monkeypatch, name):
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 3: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'news_graph_builder.settings'`.

- [ ] **Step 4: Write `settings.py`**

```python
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_GRAPH_BUILDER_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
    llm_base_uri: str
    llm_model: str
    kiwoom_app_key: SecretStr
    kiwoom_secret_key: SecretStr
    dart_api_key: SecretStr
    kiwoom_base_uri: str = "https://api.kiwoom.com"
    summary_max_chars: int = Field(default=24000, gt=0)
    llm_timeout: float = Field(default=120, gt=0)
    max_entities: int = Field(default=30, gt=0)
    max_relations: int = Field(default=50, gt=0)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder/tests/test_settings.py -v`
Expected: PASS.

- [ ] **Step 6: Wire the member into tach, Docker, compose and CI**

`tach.toml`: add `"services/news-graph-builder/src",` to `source_roots` after the clusterer's, and append:
```toml
[[modules]]
path = "news_graph_builder"
depends_on = ["ktb_core"]
```

`docker/news-graph-builder.Dockerfile`:
```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app
RUN uv venv "$VIRTUAL_ENV"

COPY docker/requirements/news-graph-builder.txt ./requirements.txt
RUN uv pip install --require-hashes --requirement requirements.txt

COPY pyproject.toml ./
COPY packages/core packages/core
COPY services/news-graph-builder services/news-graph-builder
RUN uv pip install --no-deps ./packages/core ./services/news-graph-builder

FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER app
CMD ["news-graph-builder"]
```

`compose.dev.yaml`: add after the `news-clusterer` service. Compose reads `.env` next to the compose file for `${...}` interpolation, which is where the DART key lives locally:
```yaml
  news-graph-builder:
    profiles: ["jobs"]
    build:
      context: .
      dockerfile: docker/news-graph-builder.Dockerfile
    environment:
      - NEWS_GRAPH_BUILDER_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@postgres:5432/news
      - NEWS_GRAPH_BUILDER_LLM_BASE_URI
      - NEWS_GRAPH_BUILDER_LLM_MODEL
      - NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY
      - NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY
      - NEWS_GRAPH_BUILDER_KIWOOM_BASE_URI=${NEWS_GRAPH_BUILDER_KIWOOM_BASE_URI:-https://api.kiwoom.com}
      - NEWS_GRAPH_BUILDER_DART_API_KEY=${OPENDART_API_KEY:-}
      - NEWS_GRAPH_BUILDER_LOG_LEVEL=${NEWS_GRAPH_BUILDER_LOG_LEVEL:-INFO}
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"
```

`.github/workflows/ci-dev.yaml`:
- In `verify-exported-requirements`, change the loop to `for pkg in news-preprocessor news-clusterer news-graph-builder portfolio-builder; do`.
- In `build-images`, add `- news-graph-builder` to `matrix.service` after `- news-clusterer`.

`.github/workflows/ci-main.yaml`: in `build-and-push-images`, add `- news-graph-builder` to `matrix.service` after `- news-clusterer`. Also grep the file for another hard-coded service list (`grep -n "news-clusterer" .github/workflows/ci-main.yaml`) and add `news-graph-builder` next to each occurrence.

Run:
```bash
uv export --package news-graph-builder --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/news-graph-builder.txt
uv run tach check
uv run tach check-external -e packages/market-analyzer,services
docker build -f docker/news-graph-builder.Dockerfile -t news-graph-builder:dev .
```
Expected: tach reports no errors; the image builds (it has no `__main__` yet, so do not run it).

- [ ] **Step 7: Commit**

```bash
git add services/news-graph-builder tach.toml compose.dev.yaml .github docker uv.lock
git commit -m "chore(graph-builder): scaffold the news-graph-builder service

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `normalize()`

**Files:**
- Create: `services/news-graph-builder/src/news_graph_builder/normalize.py`
- Test: `services/news-graph-builder/tests/test_normalize.py`

**Interfaces:**
- Produces: `normalize(text: str) -> str`. Used for alias seeding (Task 7), entity lookup (Task 8) and relation endpoint matching (Task 8). It may return `""`; callers skip empty names.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from news_graph_builder.normalize import normalize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("삼성전자", "삼성전자"),
        ("삼성전자(주)", "삼성전자"),
        ("(주) 삼성전자", "삼성전자"),
        ("㈜LG화학", "lg화학"),
        ("주식회사 카카오", "카카오"),
        (" SK 하이닉스\t", "sk하이닉스"),
        ("SK hynix Inc.", "skhynixinc."),
        ("(주)", ""),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTEST services/news-graph-builder/tests/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `normalize.py`**

```python
import re

CORPORATE_MARKERS = re.compile(r"\(주\)|㈜|주식회사")


def normalize(text: str) -> str:
    return "".join(CORPORATE_MARKERS.sub("", text).split()).casefold()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `PYTEST services/news-graph-builder/tests/test_normalize.py -v`
Expected: PASS (8 cases).

- [ ] **Step 5: Commit**

```bash
git add services/news-graph-builder
git commit -m "feat(graph-builder): normalize entity and company names

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Storage tables and read queries

**Files:**
- Create: `services/news-graph-builder/src/news_graph_builder/storage.py`
- Create: `services/news-graph-builder/tests/conftest.py`
- Test: `services/news-graph-builder/tests/test_storage.py`
- Modify: `infrastructure/postgres/tests/test_migrations.py`

**Interfaces:**
- Consumes: the Task 1 schema.
- Produces in `news_graph_builder.storage`: `metadata`; tables `articles`, `clusters`, `article_clusters`, `companies`, `company_aliases`, `entities`, `cluster_summaries`, `cluster_entities`, `relations`; functions
  - `due_clusters(conn: sa.Connection) -> list[tuple[int, datetime]]`
  - `cluster_articles(conn: sa.Connection, cluster_id: int) -> list[tuple[str, str]]`
  - `has_companies(conn: sa.Connection) -> bool`
- Produces fixtures in `tests/conftest.py`: `engine` (truncates all tables before and after), `article` → `add_article(conn, published_at=..., title="title", body="body") -> int`, `cluster` → `add_cluster(conn, article_ids) -> int`.

- [ ] **Step 1: Write the fixtures**

`services/news-graph-builder/tests/conftest.py`:

```python
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import count

import pytest
import sqlalchemy as sa

_external_ids = count()

TABLES = (
    "relations, cluster_entities, cluster_summaries, entities, company_aliases, companies,"
    " article_clusters, clusters, articles"
)


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    # The code under test commits, so empty the tables instead of rolling back.
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)


def add_article(
    conn: sa.Connection,
    published_at: datetime = datetime(2026, 9, 24, tzinfo=UTC),
    title: str = "title",
    body: str = "body",
) -> int:
    return conn.execute(
        sa.text(
            "INSERT INTO articles"
            " (source, external_id, url, title, body, published_at, raw_payload)"
            " VALUES ('test', :external_id, 'https://example.com', :title, :body,"
            " :published_at, '')"
            " RETURNING id"
        ),
        {
            "external_id": str(next(_external_ids)),
            "title": title,
            "body": body,
            "published_at": published_at,
        },
    ).scalar_one()


def add_cluster(conn: sa.Connection, article_ids: Iterable[int]) -> int:
    cluster_id = conn.execute(sa.text("INSERT INTO clusters DEFAULT VALUES RETURNING id")).scalar_one()
    for article_id in article_ids:
        conn.execute(
            sa.text("INSERT INTO article_clusters (article_id, cluster_id) VALUES (:a, :c)"),
            {"a": article_id, "c": cluster_id},
        )
    return cluster_id


@pytest.fixture
def article():
    return add_article


@pytest.fixture
def cluster():
    return add_cluster
```

- [ ] **Step 2: Write the failing tests**

`services/news-graph-builder/tests/test_storage.py`:

```python
from datetime import UTC, datetime

import sqlalchemy as sa
from news_graph_builder.storage import cluster_articles, due_clusters, has_companies


def summarize(conn, cluster_id: int, cluster_updated_at) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO cluster_summaries (cluster_id, title, summary, cluster_updated_at)"
            " VALUES (:id, 't', 's', :seen)"
        ),
        {"id": cluster_id, "seen": cluster_updated_at},
    )


def updated_at(conn, cluster_id: int):
    return conn.execute(
        sa.text("SELECT updated_at FROM clusters WHERE id = :id"), {"id": cluster_id}
    ).scalar_one()


def test_a_cluster_without_a_summary_is_due(engine, article, cluster):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])

    with engine.connect() as conn:
        assert due_clusters(conn) == [(cluster_id, updated_at(conn, cluster_id))]


def test_a_summary_of_the_current_updated_at_is_not_due(engine, article, cluster):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
        summarize(conn, cluster_id, updated_at(conn, cluster_id))

    with engine.connect() as conn:
        assert due_clusters(conn) == []


def test_a_summary_of_an_older_updated_at_is_due(engine, article, cluster):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
        summarize(conn, cluster_id, updated_at(conn, cluster_id))
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE clusters SET updated_at = now()"))

    with engine.connect() as conn:
        assert [row[0] for row in due_clusters(conn)] == [cluster_id]


def test_cluster_articles_are_newest_first(engine, article, cluster):
    with engine.begin() as conn:
        old = article(conn, published_at=datetime(2026, 9, 1, tzinfo=UTC), title="old")
        new = article(conn, published_at=datetime(2026, 9, 2, tzinfo=UTC), title="new")
        cluster_id = cluster(conn, [old, new])

    with engine.connect() as conn:
        assert cluster_articles(conn, cluster_id) == [("new", "body"), ("old", "body")]


def test_has_companies(engine):
    with engine.connect() as conn:
        assert has_companies(conn) is False
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO companies (corp_code, stock_code, corp_name)"
                " VALUES ('00126380', '005930', '삼성전자')"
            )
        )

    with engine.connect() as conn:
        assert has_companies(conn) is True
```

In `infrastructure/postgres/tests/test_migrations.py`, add a row to the `test_service_tables_match_the_migrated_schema` parametrize list:

```python
        (
            "news_graph_builder.storage",
            {
                "companies",
                "company_aliases",
                "entities",
                "cluster_summaries",
                "cluster_entities",
                "relations",
            },
        ),
```

- [ ] **Step 3: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/test_storage.py infrastructure -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'news_graph_builder.storage'`.

- [ ] **Step 4: Write `storage.py`**

```python
from datetime import datetime

import sqlalchemy as sa

# Mirrors infrastructure/postgres/migrations for queries only; the migrations own the schema.
metadata = sa.MetaData()

articles = sa.Table(
    "articles",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
)

clusters = sa.Table(
    "clusters",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

article_clusters = sa.Table(
    "article_clusters",
    metadata,
    sa.Column("article_id", sa.BigInteger, sa.ForeignKey("articles.id"), primary_key=True),
    sa.Column("cluster_id", sa.BigInteger, sa.ForeignKey("clusters.id"), nullable=False),
)

companies = sa.Table(
    "companies",
    metadata,
    sa.Column("corp_code", sa.Text, primary_key=True),
    sa.Column("stock_code", sa.Text, nullable=False),
    sa.Column("corp_name", sa.Text, nullable=False),
    sa.Column("corp_eng_name", sa.Text, nullable=True),
    sa.Column(
        "synced_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

company_aliases = sa.Table(
    "company_aliases",
    metadata,
    sa.Column("alias", sa.Text, primary_key=True),
    sa.Column("corp_code", sa.Text, sa.ForeignKey("companies.corp_code"), nullable=False),
)

entities = sa.Table(
    "entities",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("raw_name", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("corp_code", sa.Text, sa.ForeignKey("companies.corp_code"), nullable=True),
    sa.Index(
        "entities_corp_code_key",
        "corp_code",
        unique=True,
        postgresql_where=sa.text("corp_code IS NOT NULL"),
    ),
    sa.Index(
        "entities_name_type_key",
        "name",
        "type",
        unique=True,
        postgresql_where=sa.text("corp_code IS NULL"),
    ),
)

cluster_summaries = sa.Table(
    "cluster_summaries",
    metadata,
    sa.Column(
        "cluster_id",
        sa.BigInteger,
        sa.ForeignKey("clusters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
    sa.Column("cluster_updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "summarized_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

cluster_entities = sa.Table(
    "cluster_entities",
    metadata,
    sa.Column(
        "cluster_id",
        sa.BigInteger,
        sa.ForeignKey("clusters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), primary_key=True),
    sa.Index("cluster_entities_entity_id_idx", "entity_id"),
)

relations = sa.Table(
    "relations",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column(
        "cluster_id",
        sa.BigInteger,
        sa.ForeignKey("clusters.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("source_entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("target_entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Index("relations_cluster_id_idx", "cluster_id"),
    sa.Index("relations_source_entity_id_idx", "source_entity_id"),
    sa.Index("relations_target_entity_id_idx", "target_entity_id"),
)


def due_clusters(conn: sa.Connection) -> list[tuple[int, datetime]]:
    query = (
        sa.select(clusters.c.id, clusters.c.updated_at)
        .outerjoin(cluster_summaries, cluster_summaries.c.cluster_id == clusters.c.id)
        .where(
            sa.or_(
                cluster_summaries.c.cluster_id.is_(None),
                cluster_summaries.c.cluster_updated_at < clusters.c.updated_at,
            )
        )
        .order_by(clusters.c.id)
    )
    return [(row.id, row.updated_at) for row in conn.execute(query)]


def cluster_articles(conn: sa.Connection, cluster_id: int) -> list[tuple[str, str]]:
    query = (
        sa.select(articles.c.title, articles.c.body)
        .join(article_clusters, article_clusters.c.article_id == articles.c.id)
        .where(article_clusters.c.cluster_id == cluster_id)
        .order_by(articles.c.published_at.desc(), articles.c.id.desc())
    )
    return [(row.title, row.body) for row in conn.execute(query)]


def has_companies(conn: sa.Connection) -> bool:
    return conn.execute(sa.select(companies.c.corp_code).limit(1)).first() is not None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder/tests/test_storage.py infrastructure -v`
Expected: PASS, including `test_service_tables_match_the_migrated_schema[news_graph_builder.storage-...]`.
If only that comparison fails, and only on the partial indexes' `postgresql_where`, print `compare_metadata(...)`: Alembic may render the reflected predicate as `(corp_code IS NOT NULL)`. Make the migration and the metadata use the identical text, rather than excluding the index from the comparison.

- [ ] **Step 6: Commit**

```bash
git add services/news-graph-builder infrastructure/postgres/tests/test_migrations.py
git commit -m "feat(graph-builder): mirror the graph schema and query due clusters

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `extract()` — one LLM call for summary and graph

**Files:**
- Create: `services/news-graph-builder/src/news_graph_builder/extract.py`
- Test: `services/news-graph-builder/tests/test_extract.py`

**Interfaces:**
- Produces in `news_graph_builder.extract`:
  - `class Entity(NamedTuple): name: str; type: str`
  - `class Relation(NamedTuple): source: str; target: str; type: str; description: str`
  - `class Extraction(NamedTuple): title: str; summary: str; entities: list[Entity]; relations: list[Relation]`
  - `extract(client: httpx.Client, articles: Sequence[tuple[str, str]], *, base_uri: str, model: str, max_chars: int, timeout: float, max_entities: int, max_relations: int) -> Extraction` — raises `httpx.HTTPError` on transport/HTTP errors and `ValueError` on a reply that does not match the schema.

- [ ] **Step 1: Write the failing tests**

```python
import json

import httpx
import pytest
from news_graph_builder.extract import Entity, Extraction, Relation, extract

BASE_URI = "http://llm.test/v1"

REPLY = {
    "title": "삼성전자, 엔비디아에 HBM 공급",
    "summary": "요약",
    "entities": [{"name": "삼성전자", "type": "기업"}, {"name": "엔비디아", "type": "기업"}],
    "relations": [
        {"source": "삼성전자", "target": "엔비디아", "type": "공급", "description": "HBM 공급"}
    ],
}


def client_replying(content: str, status: int = 200, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def call(client, articles, max_chars=1000, max_entities=30, max_relations=50):
    return extract(
        client,
        articles,
        base_uri=BASE_URI,
        model="test-model",
        max_chars=max_chars,
        timeout=5,
        max_entities=max_entities,
        max_relations=max_relations,
    )


def test_returns_the_summary_and_graph():
    seen = []
    client = client_replying(json.dumps(REPLY), seen=seen)

    assert call(client, [("기사", "본문")]) == Extraction(
        "삼성전자, 엔비디아에 HBM 공급",
        "요약",
        [Entity("삼성전자", "기업"), Entity("엔비디아", "기업")],
        [Relation("삼성전자", "엔비디아", "공급", "HBM 공급")],
    )
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == f"{BASE_URI}/chat/completions"
    assert body["model"] == "test-model"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["required"] == [
        "title",
        "summary",
        "entities",
        "relations",
    ]
    assert "최대 30개" in body["messages"][0]["content"]
    assert "최대 50개" in body["messages"][0]["content"]
    assert "기사\n\n본문" in body["messages"][-1]["content"]


def test_entries_beyond_the_caps_are_cut():
    reply = dict(REPLY, relations=REPLY["relations"] * 3)

    extraction = call(client_replying(json.dumps(reply)), [("a", "b")], max_entities=1, max_relations=2)

    assert extraction.entities == [Entity("삼성전자", "기업")]
    assert len(extraction.relations) == 2


def test_budget_keeps_the_newest_article_and_stops_before_overflow():
    seen = []
    client = client_replying(json.dumps(REPLY), seen=seen)

    call(client, [("new", "x" * 50), ("old", "y" * 50)], max_chars=80)

    prompt = json.loads(seen[0].content)["messages"][-1]["content"]
    assert "new\n\n" in prompt
    assert "old" not in prompt


def test_the_newest_article_is_truncated_to_the_budget():
    seen = []
    client = client_replying(json.dumps(REPLY), seen=seen)

    call(client, [("new", "x" * 500)], max_chars=20)

    prompt = json.loads(seen[0].content)["messages"][-1]["content"]
    # "new\n\n" takes 5 of the 20 characters.
    assert "x" * 15 in prompt
    assert "x" * 16 not in prompt


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps([REPLY]),
        json.dumps({k: v for k, v in REPLY.items() if k != "entities"}),
        json.dumps(dict(REPLY, entities=[{"name": "삼성전자"}])),
        json.dumps(dict(REPLY, entities="삼성전자")),
        json.dumps(dict(REPLY, title=1)),
        json.dumps(dict(REPLY, relations=[dict(REPLY["relations"][0], description=None)])),
    ],
)
def test_malformed_replies_raise_value_error(content):
    with pytest.raises(ValueError):
        call(client_replying(content), [("a", "b")])


def test_http_errors_raise():
    with pytest.raises(httpx.HTTPStatusError):
        call(client_replying("", status=500), [("a", "b")])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `extract.py`**

```python
import json
from collections.abc import Sequence
from typing import NamedTuple

import httpx

SYSTEM_PROMPT = (
    "당신은 경제 뉴스 편집자입니다. 같은 사건을 다룬 기사들이 주어집니다. "
    "사건을 대표하는 한 줄 제목과 3~5문장 요약을 한국어로 작성하세요. "
    "기사에 나오는 주요 개체(기업, 인물, 기관, 국가, 정책, 제품 등)를 최대 {max_entities}개, "
    "개체 사이의 관계를 최대 {max_relations}개 한국어로 추출하세요. "
    "관계의 source와 target에는 entities의 name을 그대로 쓰세요."
)

STRING = {"type": "string"}


def _object(*names: str) -> dict:
    return {
        "type": "object",
        "properties": dict.fromkeys(names, STRING),
        "required": list(names),
        "additionalProperties": False,
    }


RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "cluster_graph",
        "schema": {
            "type": "object",
            "properties": {
                "title": STRING,
                "summary": STRING,
                "entities": {"type": "array", "items": _object("name", "type")},
                "relations": {
                    "type": "array",
                    "items": _object("source", "target", "type", "description"),
                },
            },
            "required": ["title", "summary", "entities", "relations"],
            "additionalProperties": False,
        },
    },
}


class Entity(NamedTuple):
    name: str
    type: str


class Relation(NamedTuple):
    source: str
    target: str
    type: str
    description: str


class Extraction(NamedTuple):
    title: str
    summary: str
    entities: list[Entity]
    relations: list[Relation]


def extract(
    client: httpx.Client,
    articles: Sequence[tuple[str, str]],
    *,
    base_uri: str,
    model: str,
    max_chars: int,
    timeout: float,
    max_entities: int,
    max_relations: int,
) -> Extraction:
    blocks: list[str] = []
    used = 0
    for title, body in articles:
        block = f"{title}\n\n{body}"
        if blocks and used + len(block) > max_chars:
            break
        blocks.append(block[:max_chars])
        used += len(block)

    response = client.post(
        f"{base_uri.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT.format(
                        max_entities=max_entities, max_relations=max_relations
                    ),
                },
                {"role": "user", "content": "\n\n---\n\n".join(blocks)},
            ],
            "response_format": RESPONSE_FORMAT,
        },
        timeout=timeout,
    )
    content = response.raise_for_status().json()["choices"][0]["message"]["content"]
    try:
        reply = json.loads(content)
        extraction = Extraction(
            reply["title"],
            reply["summary"],
            [Entity(item["name"], item["type"]) for item in reply["entities"]][:max_entities],
            [
                Relation(item["source"], item["target"], item["type"], item["description"])
                for item in reply["relations"]
            ][:max_relations],
        )
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"LLM reply does not match the schema: {content!r}") from error
    fields = [
        extraction.title,
        extraction.summary,
        *(field for entity in extraction.entities for field in entity),
        *(field for relation in extraction.relations for field in relation),
    ]
    if not all(isinstance(field, str) for field in fields):
        raise ValueError(f"LLM reply has non-string fields: {content!r}")
    return extraction
```

`_object` has two callers (the entity and relation item schemas), so it is not a single-caller helper.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder/tests/test_extract.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/news-graph-builder
git commit -m "feat(graph-builder): extract a summary and graph from a cluster in one LLM call

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Kiwoom and DART fetchers

**Files:**
- Create: `services/news-graph-builder/src/news_graph_builder/kiwoom.py`
- Create: `services/news-graph-builder/src/news_graph_builder/dart.py`
- Test: `services/news-graph-builder/tests/test_kiwoom.py`, `services/news-graph-builder/tests/test_dart.py`

**Interfaces:**
- Produces:
  - `fetch_kospi(client: httpx.Client, *, base_uri: str, app_key: str, secret_key: str) -> list[tuple[str, str]]` — `(stock_code, name)` rows; raises `RuntimeError` on a non-zero Kiwoom `return_code`, `httpx.HTTPError` on HTTP errors.
  - `class DartCompany(NamedTuple): corp_code: str; corp_name: str; corp_eng_name: str | None; stock_code: str`
  - `fetch_corp_codes(api_key: str) -> list[DartCompany]` — listed companies only; raises `RuntimeError` whose message and traceback never contain the key.

- [ ] **Step 1: Write the failing Kiwoom tests**

`tests/test_kiwoom.py`:

```python
import json

import httpx
import pytest
from news_graph_builder.kiwoom import fetch_kospi

BASE_URI = "https://kiwoom.test"


def kiwoom(pages: list[tuple[list[dict], dict]], token_reply: dict | None = None, seen=None):
    remaining = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=token_reply or {"return_code": 0, "token": "tok"})
        rows, headers = remaining.pop(0)
        return httpx.Response(
            200, json={"return_code": 0, "return_msg": "ok", "list": rows}, headers=headers
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def fetch(client):
    return fetch_kospi(client, base_uri=BASE_URI, app_key="app", secret_key="secret")


def test_follows_continuation_pages():
    seen = []
    client = kiwoom(
        [
            ([{"code": "005930", "name": "삼성전자"}], {"cont-yn": "Y", "next-key": "k1"}),
            ([{"code": "000660", "name": "SK하이닉스"}], {"cont-yn": "N", "next-key": ""}),
        ],
        seen=seen,
    )

    assert fetch(client) == [("005930", "삼성전자"), ("000660", "SK하이닉스")]

    token, first, second = seen
    assert json.loads(token.content) == {
        "grant_type": "client_credentials",
        "appkey": "app",
        "secretkey": "secret",
    }
    assert first.url.path == "/api/dostk/stkinfo"
    assert first.headers["api-id"] == "ka10099"
    assert first.headers["authorization"] == "Bearer tok"
    assert first.headers["cont-yn"] == "N"
    assert json.loads(first.content) == {"mrkt_tp": "0"}
    assert (second.headers["cont-yn"], second.headers["next-key"]) == ("Y", "k1")


def test_a_refused_token_raises():
    client = kiwoom([], token_reply={"return_code": 3, "return_msg": "invalid appkey"})

    with pytest.raises(RuntimeError, match="invalid appkey"):
        fetch(client)


def test_a_failed_page_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"return_code": 0, "token": "tok"})
        return httpx.Response(200, json={"return_code": 5, "return_msg": "rate limited"})

    with pytest.raises(RuntimeError, match="rate limited"):
        fetch(httpx.Client(transport=httpx.MockTransport(handler)))
```

- [ ] **Step 2: Write the failing DART tests**

`tests/test_dart.py`:

```python
import traceback

import pandas as pd
import pytest
from news_graph_builder import dart
from news_graph_builder.dart import DartCompany, fetch_corp_codes


def test_keeps_listed_companies(monkeypatch):
    frame = pd.DataFrame(
        [
            ["00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930", "20251201"],
            ["00999999", "비상장", "Unlisted", " ", "20250101"],
            ["00164779", "SK하이닉스", " ", "000660", "20240328"],
            ["00266961", "NAVER", None, "035420", "20240311"],
            ["00888888", "상장폐지", "Delisted", None, "20240101"],
        ],
        columns=["corp_code", "corp_name", "corp_eng_name", "stock_code", "modify_date"],
    )
    monkeypatch.setattr(dart, "corp_codes", lambda api_key: frame)

    assert fetch_corp_codes("key") == [
        DartCompany("00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930"),
        DartCompany("00164779", "SK하이닉스", None, "000660"),
        DartCompany("00266961", "NAVER", None, "035420"),
    ]


def test_errors_never_carry_the_key(monkeypatch):
    def failing(api_key):
        raise OSError(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={api_key}")

    monkeypatch.setattr(dart, "corp_codes", failing)

    with pytest.raises(RuntimeError) as info:
        fetch_corp_codes("SECRET")

    assert "SECRET" not in "".join(traceback.format_exception(info.value))


def test_dart_status_errors_keep_their_message(monkeypatch):
    def refusing(api_key):
        raise ValueError({"status": "020", "message": "요청 제한을 초과하였습니다."})

    monkeypatch.setattr(dart, "corp_codes", refusing)

    with pytest.raises(RuntimeError, match="020"):
        fetch_corp_codes("key")
```

- [ ] **Step 3: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/test_kiwoom.py services/news-graph-builder/tests/test_dart.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write `kiwoom.py`**

```python
import httpx


def fetch_kospi(
    client: httpx.Client, *, base_uri: str, app_key: str, secret_key: str
) -> list[tuple[str, str]]:
    base_uri = base_uri.rstrip("/")
    headers = {"content-type": "application/json;charset=UTF-8"}
    reply = (
        client.post(
            f"{base_uri}/oauth2/token",
            json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": secret_key},
            headers=headers,
        )
        .raise_for_status()
        .json()
    )
    if "token" not in reply:
        raise RuntimeError(
            f"Kiwoom token refused: {reply.get('return_code')} {reply.get('return_msg')}"
        )

    headers |= {
        "authorization": f"Bearer {reply['token']}",
        "api-id": "ka10099",
        "cont-yn": "N",
        "next-key": "",
    }
    rows: list[tuple[str, str]] = []
    while True:
        response = client.post(
            f"{base_uri}/api/dostk/stkinfo", json={"mrkt_tp": "0"}, headers=headers
        ).raise_for_status()
        page = response.json()
        if page.get("return_code") != 0:
            raise RuntimeError(
                f"Kiwoom ka10099 failed: {page.get('return_code')} {page.get('return_msg')}"
            )
        rows += [(item["code"], item["name"]) for item in page["list"]]
        if response.headers.get("cont-yn") != "Y":
            return rows
        headers |= {"cont-yn": "Y", "next-key": response.headers["next-key"]}
```

- [ ] **Step 5: Write `dart.py`**

```python
from typing import NamedTuple

from opendartreader.dart_list import corp_codes


class DartCompany(NamedTuple):
    corp_code: str
    corp_name: str
    corp_eng_name: str | None
    stock_code: str


def fetch_corp_codes(api_key: str) -> list[DartCompany]:
    try:
        frame = corp_codes(api_key)
    except Exception as error:
        # requests puts the request URL, which carries the key, into its error messages.
        # DART status errors are a ValueError holding a {'status', 'message'} dict.
        detail = (
            error.args[0]
            if isinstance(error, ValueError) and error.args and isinstance(error.args[0], dict)
            else type(error).__name__
        )
        raise RuntimeError(f"DART corp_codes failed: {detail}") from None
    return [
        DartCompany(
            row.corp_code,
            row.corp_name,
            row.corp_eng_name.strip() or None,
            row.stock_code.strip(),
        )
        # pandas 3 stores a missing string as NaN, which is truthy and has no strip().
        for row in frame.fillna("").itertuples()
        if row.stock_code.strip()
    ]
```

`corp_codes` is imported from `opendartreader.dart_list` rather than going through the `OpenDartReader` constructor. The constructor loads `.env` from the working directory and writes a pickle cache to `./docs_cache/`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder/tests/test_kiwoom.py services/news-graph-builder/tests/test_dart.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/news-graph-builder
git commit -m "feat(graph-builder): fetch KOSPI codes from Kiwoom and corp codes from DART

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `sync_companies()` — join, upsert, aliases and merge

**Files:**
- Modify: `services/news-graph-builder/src/news_graph_builder/storage.py` (add `COMPANY_TYPE`, `company_entity_id`)
- Create: `services/news-graph-builder/src/news_graph_builder/sync_companies.py`
- Test: `services/news-graph-builder/tests/test_sync_companies.py`

**Interfaces:**
- Consumes: `DartCompany` (Task 6), `normalize` (Task 3), storage tables (Task 4).
- Produces:
  - `storage.COMPANY_TYPE = "기업"`
  - `storage.company_entity_id(conn: sa.Connection, corp_code: str) -> int` — the company's entity id, created on first use.
  - `sync_companies(conn: sa.Connection, kospi: Sequence[tuple[str, str]], dart: Sequence[DartCompany]) -> tuple[int, int]` — `(joined, merged)`; raises `ValueError` when no KOSPI code matches a DART `stock_code`.

- [ ] **Step 1: Write the failing tests**

`tests/test_sync_companies.py`:

```python
import pytest
import sqlalchemy as sa
from news_graph_builder.dart import DartCompany
from news_graph_builder.sync_companies import sync_companies

SAMSUNG = DartCompany("00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930")
HYNIX = DartCompany("00164779", "SK하이닉스", "SK hynix Inc.", "000660")
KOSDAQ = DartCompany("00111111", "코스닥기업", None, "111111")
KOSPI = [("005930", "삼성전자"), ("005935", "삼성전자우"), ("000660", "SK하이닉스")]


def sync(engine, kospi=KOSPI, dart=(SAMSUNG, HYNIX, KOSDAQ)):
    with engine.begin() as conn:
        return sync_companies(conn, kospi, dart)


def rows(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).all()


def test_joins_kospi_codes_to_dart_and_seeds_aliases(engine):
    assert sync(engine) == (2, 0)

    assert rows(engine, "SELECT corp_code, stock_code, corp_name FROM companies ORDER BY 1") == [
        ("00126380", "005930", "삼성전자"),
        ("00164779", "000660", "SK하이닉스"),
    ]
    assert dict(rows(engine, "SELECT alias, corp_code FROM company_aliases")) == {
        "삼성전자": "00126380",
        "samsungelectronicsco,.ltd": "00126380",
        "sk하이닉스": "00164779",
        "skhynixinc.": "00164779",
    }


def test_no_joined_rows_raises(engine):
    with pytest.raises(ValueError):
        sync(engine, kospi=[("A005930", "삼성전자")])


def test_a_shared_alias_keeps_its_first_owner(engine):
    sync(engine, kospi=[("005930", "공유이름"), ("000660", "공유이름")])

    assert rows(engine, "SELECT corp_code FROM company_aliases WHERE alias = '공유이름'") == [
        ("00126380",)
    ]


def test_a_second_sync_updates_names_without_duplicating(engine):
    sync(engine)
    (before,) = rows(engine, "SELECT synced_at FROM companies WHERE corp_code = '00126380'")

    sync(engine, dart=(SAMSUNG._replace(corp_name="삼성전자신"), HYNIX))

    assert rows(engine, "SELECT count(*) FROM companies") == [(2,)]
    (name, synced_at) = rows(
        engine, "SELECT corp_name, synced_at FROM companies WHERE corp_code = '00126380'"
    )[0]
    assert name == "삼성전자신"
    assert synced_at > before[0]
    assert rows(engine, "SELECT corp_code FROM company_aliases WHERE alias = '삼성전자'") == [
        ("00126380",)
    ]


def test_merges_plain_entities_into_a_newly_aliased_company(engine, article, cluster):
    sync(engine)
    with engine.begin() as conn:
        first = cluster(conn, [article(conn)])
        second = cluster(conn, [article(conn)])
        as_company, as_firm, nvidia = (
            conn.execute(
                sa.text(
                    "INSERT INTO entities (raw_name, name, type) VALUES (:raw, :name, :type)"
                    " RETURNING id"
                ),
                {"raw": raw, "name": name, "type": type_},
            ).scalar_one()
            for raw, name, type_ in [
                ("하이닉스", "하이닉스", "회사"),
                ("하이닉스", "하이닉스", "기업"),
                ("엔비디아", "엔비디아", "기업"),
            ]
        )
        for cluster_id, entity_id in [
            (first, as_company),
            (first, as_firm),
            (first, nvidia),
            (second, as_company),
        ]:
            conn.execute(
                sa.text("INSERT INTO cluster_entities VALUES (:c, :e)"),
                {"c": cluster_id, "e": entity_id},
            )
        conn.execute(
            sa.text(
                "INSERT INTO relations (cluster_id, source_entity_id, target_entity_id, type,"
                " description) VALUES (:c, :a, :n, '공급', ''), (:c, :n, :b, '구매', '')"
            ),
            {"c": first, "a": as_company, "b": as_firm, "n": nvidia},
        )
        conn.execute(sa.text("INSERT INTO company_aliases VALUES ('하이닉스', '00164779')"))

    assert sync(engine) == (2, 2)

    (company,) = rows(engine, "SELECT id FROM entities WHERE corp_code = '00164779'")[0]
    assert rows(engine, "SELECT id FROM entities WHERE corp_code IS NULL") == [(nvidia,)]
    assert set(rows(engine, "SELECT cluster_id, entity_id FROM cluster_entities")) == {
        (first, company),
        (first, nvidia),
        (second, company),
    }
    assert set(rows(engine, "SELECT source_entity_id, target_entity_id FROM relations")) == {
        (company, nvidia),
        (nvidia, company),
    }
    assert sync(engine) == (2, 0)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/test_sync_companies.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'news_graph_builder.sync_companies'`.

- [ ] **Step 3: Add `company_entity_id` to `storage.py`**

Add to the imports of `storage.py`:
```python
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.normalize import normalize
```
Add below the tables:
```python
COMPANY_TYPE = "기업"
```
Append:
```python
def company_entity_id(conn: sa.Connection, corp_code: str) -> int:
    corp_name = conn.execute(
        sa.select(companies.c.corp_name).where(companies.c.corp_code == corp_code)
    ).scalar_one()
    statement = insert(entities).values(
        raw_name=corp_name, name=normalize(corp_name), type=COMPANY_TYPE, corp_code=corp_code
    )
    return conn.execute(
        statement.on_conflict_do_update(
            index_elements=[entities.c.corp_code],
            index_where=entities.c.corp_code.is_not(None),
            # A no-op update, so that RETURNING also yields the id of an existing row.
            set_={"corp_code": statement.excluded.corp_code},
        ).returning(entities.c.id)
    ).scalar_one()
```

- [ ] **Step 4: Write `sync_companies.py`**

```python
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.dart import DartCompany
from news_graph_builder.normalize import normalize
from news_graph_builder.storage import (
    cluster_entities,
    companies,
    company_aliases,
    company_entity_id,
    entities,
    relations,
)


def sync_companies(
    conn: sa.Connection, kospi: Sequence[tuple[str, str]], dart: Sequence[DartCompany]
) -> tuple[int, int]:
    by_stock_code = {company.stock_code: company for company in dart}
    joined = {
        by_stock_code[code].corp_code: (by_stock_code[code], name)
        for code, name in kospi
        if code in by_stock_code
    }
    if not joined:
        raise ValueError(f"none of {len(kospi)} KOSPI codes matched a DART stock_code")

    statement = insert(companies).values(
        [
            {
                "corp_code": company.corp_code,
                "stock_code": company.stock_code,
                "corp_name": company.corp_name,
                "corp_eng_name": company.corp_eng_name,
            }
            for company, _ in joined.values()
        ]
    )
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=[companies.c.corp_code],
            set_={
                "stock_code": statement.excluded.stock_code,
                "corp_name": statement.excluded.corp_name,
                "corp_eng_name": statement.excluded.corp_eng_name,
                "synced_at": sa.func.now(),
            },
        )
    )
    aliases = [
        {"alias": alias, "corp_code": company.corp_code}
        for company, kiwoom_name in joined.values()
        for alias in dict.fromkeys(
            normalize(name) for name in (company.corp_name, kiwoom_name, company.corp_eng_name or "")
        )
        if alias
    ]
    conn.execute(insert(company_aliases).values(aliases).on_conflict_do_nothing())

    plain = conn.execute(
        sa.select(entities.c.id, company_aliases.c.corp_code)
        .join(company_aliases, company_aliases.c.alias == entities.c.name)
        .where(entities.c.corp_code.is_(None))
    ).all()
    for entity_id, corp_code in plain:
        company_id = company_entity_id(conn, corp_code)
        for column in (relations.c.source_entity_id, relations.c.target_entity_id):
            conn.execute(sa.update(relations).where(column == entity_id).values({column: company_id}))
        conn.execute(
            insert(cluster_entities)
            .from_select(
                ["cluster_id", "entity_id"],
                sa.select(
                    cluster_entities.c.cluster_id, sa.literal(company_id, sa.BigInteger)
                ).where(cluster_entities.c.entity_id == entity_id),
            )
            .on_conflict_do_nothing()
        )
        conn.execute(sa.delete(cluster_entities).where(cluster_entities.c.entity_id == entity_id))
        conn.execute(sa.delete(entities).where(entities.c.id == entity_id))
    return len(joined), len(plain)
```

`dict.fromkeys` removes a company's duplicate aliases while keeping their order. `ON CONFLICT DO NOTHING` then leaves an alias with the company that claimed it first, whether in an earlier sync or earlier in this batch.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/news-graph-builder
git commit -m "feat(graph-builder): sync KOSPI companies, seed aliases and merge plain entities

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `resolve()` and the guarded graph write

**Files:**
- Create: `services/news-graph-builder/src/news_graph_builder/resolve.py`
- Modify: `services/news-graph-builder/src/news_graph_builder/storage.py` (add `lock_cluster`, `write_graph`)
- Test: `services/news-graph-builder/tests/test_resolve.py`, add to `tests/test_storage.py`

**Interfaces:**
- Consumes: `Entity`, `Extraction` (Task 5), `company_entity_id` (Task 7), `sync_companies` (Task 7, in tests).
- Produces:
  - `resolve(conn: sa.Connection, llm_entities: Sequence[Entity]) -> dict[str, int]` — normalized LLM name → entity id; empty names are skipped; the first entity per normalized name wins.
  - `storage.lock_cluster(conn: sa.Connection, cluster_id: int, seen: datetime) -> bool` — `FOR SHARE` lock; `False` when the cluster is gone or its `updated_at` moved.
  - `storage.write_graph(conn: sa.Connection, cluster_id: int, seen: datetime, extraction: Extraction, entity_ids: dict[str, int]) -> int` — replaces the cluster's graph, upserts its summary, returns the number of dropped relations.

- [ ] **Step 1: Write the failing resolve tests**

`tests/test_resolve.py`:

```python
import sqlalchemy as sa
from news_graph_builder.dart import DartCompany
from news_graph_builder.extract import Entity
from news_graph_builder.resolve import resolve
from news_graph_builder.sync_companies import sync_companies

SAMSUNG = DartCompany("00126380", "삼성전자", None, "005930")


def resolve_in(engine, *llm_entities):
    with engine.begin() as conn:
        return resolve(conn, llm_entities)


def test_an_alias_hit_ignores_the_llm_type(engine):
    with engine.begin() as conn:
        sync_companies(conn, [("005930", "삼성전자")], [SAMSUNG])

    first = resolve_in(engine, Entity("삼성전자(주)", "회사"))
    second = resolve_in(engine, Entity("삼성전자", "company"))

    assert first == second == {"삼성전자": first["삼성전자"]}
    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT corp_code, type FROM entities")).one()
    assert tuple(row) == ("00126380", "기업")


def test_a_miss_creates_one_entity_per_name_and_type(engine):
    bank = resolve_in(engine, Entity("한국은행", "기관"))["한국은행"]

    assert resolve_in(engine, Entity("한국 은행", "기관")) == {"한국은행": bank}
    assert resolve_in(engine, Entity("한국은행", "정부"))["한국은행"] != bank
    with engine.connect() as conn:
        raw = conn.execute(
            sa.text("SELECT raw_name FROM entities WHERE id = :id"), {"id": bank}
        ).scalar_one()
    assert raw == "한국은행"


def test_duplicate_and_empty_names_are_resolved_once(engine):
    resolved = resolve_in(
        engine, Entity("엔비디아", "기업"), Entity("엔비디아", "회사"), Entity("(주)", "기업")
    )

    assert list(resolved) == ["엔비디아"]
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM entities")).scalar_one() == 1
```

- [ ] **Step 2: Write the failing write tests**

Append to `tests/test_storage.py` (extend its imports as shown):

```python
from news_graph_builder.extract import Entity, Extraction, Relation
from news_graph_builder.resolve import resolve
from news_graph_builder.storage import lock_cluster, write_graph

EXTRACTION = Extraction(
    "제목",
    "요약",
    [Entity("삼성전자", "기업"), Entity("엔비디아", "기업")],
    [
        Relation("삼성전자", "엔비디아", "공급", "HBM 공급"),
        Relation("삼성전자", "애플", "경쟁", "없는 개체"),
    ],
)


def build(engine, cluster_id: int, extraction=EXTRACTION) -> int:
    with engine.begin() as conn:
        seen = updated_at(conn, cluster_id)
        assert lock_cluster(conn, cluster_id, seen)
        return write_graph(conn, cluster_id, seen, extraction, resolve(conn, extraction.entities))


def test_write_graph_stores_the_summary_and_drops_dangling_relations(engine, article, cluster):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])

    assert build(engine, cluster_id) == 1

    with engine.connect() as conn:
        summary = conn.execute(sa.text("SELECT * FROM cluster_summaries")).one()
        assert (summary.title, summary.summary) == ("제목", "요약")
        assert summary.cluster_updated_at == updated_at(conn, cluster_id)
        assert conn.execute(sa.text("SELECT count(*) FROM cluster_entities")).scalar_one() == 2
        assert conn.execute(sa.text("SELECT type FROM relations")).scalars().all() == ["공급"]
        assert due_clusters(conn) == []


def test_rewriting_a_cluster_replaces_its_graph(engine, article, cluster):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
    build(engine, cluster_id)

    build(
        engine,
        cluster_id,
        Extraction("새 제목", "새 요약", [Entity("엔비디아", "기업")], []),
    )

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT title FROM cluster_summaries")).scalar_one() == "새 제목"
        assert conn.execute(sa.text("SELECT count(*) FROM cluster_entities")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM relations")).scalar_one() == 0


def test_lock_fails_when_the_cluster_changed_or_vanished(engine, article, cluster):
    with engine.begin() as conn:
        moved = cluster(conn, [article(conn)])
        gone = cluster(conn, [article(conn)])
        seen = {cluster_id: updated_at(conn, cluster_id) for cluster_id in (moved, gone)}
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE clusters SET updated_at = now() WHERE id = :id"), {"id": moved})
        conn.execute(sa.text("DELETE FROM clusters WHERE id = :id"), {"id": gone})

    with engine.begin() as conn:
        assert lock_cluster(conn, moved, seen[moved]) is False
        assert lock_cluster(conn, gone, seen[gone]) is False


def test_a_change_after_the_write_makes_the_cluster_due_again(engine, article, cluster):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
    build(engine, cluster_id)
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE clusters SET updated_at = now()"))

    with engine.connect() as conn:
        assert [row[0] for row in due_clusters(conn)] == [cluster_id]


def test_deleting_a_cluster_cascades_to_its_graph(engine, article, cluster):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
    build(engine, cluster_id)

    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM clusters"))

    with engine.connect() as conn:
        for table in ("cluster_summaries", "cluster_entities", "relations"):
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT count(*) FROM entities")).scalar_one() == 2
```

- [ ] **Step 3: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/test_resolve.py services/news-graph-builder/tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'news_graph_builder.resolve'`.

- [ ] **Step 4: Write `resolve.py`**

```python
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.extract import Entity
from news_graph_builder.normalize import normalize
from news_graph_builder.storage import company_aliases, company_entity_id, entities


def resolve(conn: sa.Connection, llm_entities: Sequence[Entity]) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for entity in llm_entities:
        name = normalize(entity.name)
        if not name or name in resolved:
            continue
        corp_code = conn.execute(
            sa.select(company_aliases.c.corp_code).where(company_aliases.c.alias == name)
        ).scalar()
        if corp_code is not None:
            resolved[name] = company_entity_id(conn, corp_code)
            continue
        statement = insert(entities).values(
            raw_name=entity.name.strip(), name=name, type=entity.type.strip()
        )
        resolved[name] = conn.execute(
            statement.on_conflict_do_update(
                index_elements=[entities.c.name, entities.c.type],
                index_where=entities.c.corp_code.is_(None),
                # A no-op update, so that RETURNING also yields the id of an existing row.
                set_={"name": statement.excluded.name},
            ).returning(entities.c.id)
        ).scalar_one()
    return resolved
```

- [ ] **Step 5: Add `lock_cluster` and `write_graph` to `storage.py`**

Add `from news_graph_builder.extract import Extraction` to the imports, then append:

```python
def lock_cluster(conn: sa.Connection, cluster_id: int, seen: datetime) -> bool:
    query = (
        sa.select(clusters.c.id)
        .where(clusters.c.id == cluster_id, clusters.c.updated_at == seen)
        .with_for_update(read=True)
    )
    return conn.execute(query).first() is not None


def write_graph(
    conn: sa.Connection,
    cluster_id: int,
    seen: datetime,
    extraction: Extraction,
    entity_ids: dict[str, int],
) -> int:
    conn.execute(sa.delete(cluster_entities).where(cluster_entities.c.cluster_id == cluster_id))
    conn.execute(sa.delete(relations).where(relations.c.cluster_id == cluster_id))
    if entity_ids:
        conn.execute(
            insert(cluster_entities),
            [
                {"cluster_id": cluster_id, "entity_id": entity_id}
                for entity_id in sorted(set(entity_ids.values()))
            ],
        )
    rows = [
        {
            "cluster_id": cluster_id,
            "source_entity_id": entity_ids[normalize(relation.source)],
            "target_entity_id": entity_ids[normalize(relation.target)],
            "type": relation.type,
            "description": relation.description,
        }
        for relation in extraction.relations
        if normalize(relation.source) in entity_ids and normalize(relation.target) in entity_ids
    ]
    if rows:
        conn.execute(insert(relations), rows)
    statement = insert(cluster_summaries).values(
        cluster_id=cluster_id,
        title=extraction.title,
        summary=extraction.summary,
        cluster_updated_at=seen,
    )
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=[cluster_summaries.c.cluster_id],
            set_={
                "title": statement.excluded.title,
                "summary": statement.excluded.summary,
                "cluster_updated_at": statement.excluded.cluster_updated_at,
                "summarized_at": sa.func.now(),
            },
        )
    )
    return len(extraction.relations) - len(rows)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder infrastructure -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add services/news-graph-builder
git commit -m "feat(graph-builder): resolve entities and write a cluster graph under an optimistic lock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `main()` — the run flow

**Files:**
- Create: `services/news-graph-builder/src/news_graph_builder/__main__.py`
- Test: `services/news-graph-builder/tests/test_main.py`

**Interfaces:**
- Consumes: every function above, by the exact names in their Interfaces blocks.
- Produces: `main() -> None`, which always ends in `sys.exit(0 | 1)`; the console script `news-graph-builder`.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
import sqlalchemy as sa
from news_graph_builder import __main__ as entry
from news_graph_builder.dart import DartCompany
from news_graph_builder.extract import Entity, Extraction, Relation
from news_graph_builder.storage import due_clusters

SAMSUNG = DartCompany("00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930")
EXTRACTION = Extraction(
    "제목",
    "요약",
    [Entity("삼성전자", "회사"), Entity("엔비디아", "기업")],
    [Relation("삼성전자", "엔비디아", "공급", "HBM 공급")],
)


@pytest.fixture
def env(monkeypatch, pg_dsn):
    for name, value in {
        "POSTGRES_DSN": pg_dsn,
        "LLM_BASE_URI": "http://llm.test/v1",
        "LLM_MODEL": "test-model",
        "KIWOOM_APP_KEY": "app",
        "KIWOOM_SECRET_KEY": "secret",
        "DART_API_KEY": "dart",
    }.items():
        monkeypatch.setenv(f"NEWS_GRAPH_BUILDER_{name}", value)
    # setup_logging replaces the root handlers, which would detach caplog.
    monkeypatch.setattr(entry, "setup_logging", lambda level: None)


@pytest.fixture
def companies_api(monkeypatch):
    monkeypatch.setattr(entry, "fetch_kospi", lambda client, **kwargs: [("005930", "삼성전자")])
    monkeypatch.setattr(entry, "fetch_corp_codes", lambda api_key: [SAMSUNG])


@pytest.fixture
def llm(monkeypatch):
    calls = []

    def fake(client, articles, **kwargs):
        calls.append(articles)
        return EXTRACTION

    monkeypatch.setattr(entry, "extract", fake)
    return calls


@pytest.fixture
def two_clusters(engine, article, cluster):
    with engine.begin() as conn:
        cluster(conn, [article(conn), article(conn)])
        cluster(conn, [article(conn)])


def run() -> int:
    with pytest.raises(SystemExit) as exit_info:
        entry.main()
    return exit_info.value.code


def count(engine, table: str) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


def test_builds_a_graph_for_every_due_cluster(env, engine, two_clusters, companies_api, llm):
    assert run() == 0

    assert len(llm) == 2
    assert count(engine, "cluster_summaries") == 2
    assert count(engine, "relations") == 2
    with engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT corp_code FROM entities WHERE name = '삼성전자'")
        ).scalar_one() == "00126380"


def test_a_second_run_makes_no_llm_call(env, engine, two_clusters, companies_api, llm):
    assert run() == 0

    assert run() == 0

    assert len(llm) == 2


def test_a_failed_extraction_exits_1_and_is_retried(
    env, engine, two_clusters, companies_api, monkeypatch
):
    def broken(client, articles, **kwargs):
        raise ValueError("bad reply")

    monkeypatch.setattr(entry, "extract", broken)
    assert run() == 1
    assert count(engine, "cluster_summaries") == 0

    monkeypatch.setattr(entry, "extract", lambda client, articles, **kwargs: EXTRACTION)
    assert run() == 0
    assert count(engine, "cluster_summaries") == 2


def test_a_failed_first_sync_exits_before_any_llm_call(env, engine, two_clusters, llm, monkeypatch):
    def unreachable(client, **kwargs):
        raise RuntimeError("Kiwoom down")

    monkeypatch.setattr(entry, "fetch_kospi", unreachable)
    monkeypatch.setattr(entry, "fetch_corp_codes", lambda api_key: [SAMSUNG])

    assert run() == 1
    assert llm == []


def test_a_failed_later_sync_still_builds_but_exits_1(
    env, engine, article, cluster, companies_api, llm, monkeypatch
):
    assert run() == 0
    with engine.begin() as conn:
        cluster(conn, [article(conn)])

    def unreachable(api_key):
        raise RuntimeError("DART down")

    monkeypatch.setattr(entry, "fetch_corp_codes", unreachable)

    assert run() == 1
    assert len(llm) == 1
    assert count(engine, "cluster_summaries") == 1


def test_a_cluster_changed_during_extraction_is_skipped(
    env, engine, two_clusters, companies_api, monkeypatch
):
    def racing(client, articles, **kwargs):
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE clusters SET updated_at = now()"))
        return EXTRACTION

    monkeypatch.setattr(entry, "extract", racing)

    assert run() == 0
    assert count(engine, "cluster_summaries") == 0
    with engine.connect() as conn:
        assert len(due_clusters(conn)) == 2
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTEST services/news-graph-builder/tests/test_main.py -v`
Expected: FAIL with `ImportError` (no `news_graph_builder.__main__`).

- [ ] **Step 3: Write `__main__.py`**

```python
import logging
import sys
from contextlib import ExitStack

import httpx
import sqlalchemy as sa
from ktb_core.logging import setup_logging

from news_graph_builder.dart import fetch_corp_codes
from news_graph_builder.extract import extract
from news_graph_builder.kiwoom import fetch_kospi
from news_graph_builder.resolve import resolve
from news_graph_builder.settings import Settings
from news_graph_builder.storage import (
    cluster_articles,
    due_clusters,
    has_companies,
    lock_cluster,
    write_graph,
)
from news_graph_builder.sync_companies import sync_companies

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-graph-builder started")
    sync_failed = False
    failed = 0
    with httpx.Client() as client, ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
        try:
            kospi = fetch_kospi(
                client,
                base_uri=settings.kiwoom_base_uri,
                app_key=settings.kiwoom_app_key.get_secret_value(),
                secret_key=settings.kiwoom_secret_key.get_secret_value(),
            )
            dart = fetch_corp_codes(settings.dart_api_key.get_secret_value())
            with engine.begin() as conn:
                joined, merged = sync_companies(conn, kospi, dart)
            logger.info(
                "synced companies: kiwoom=%d dart=%d joined=%d merged=%d",
                len(kospi),
                len(dart),
                joined,
                merged,
            )
        except Exception:
            logger.exception("company sync failed")
            sync_failed = True
            with engine.connect() as conn:
                if not has_companies(conn):
                    # Without companies every company would become a plain entity for good.
                    sys.exit(1)

        with engine.connect() as conn:
            due = due_clusters(conn)
        for cluster_id, seen in due:
            try:
                with engine.connect() as conn:
                    articles = cluster_articles(conn, cluster_id)
                if not articles:
                    continue
                extraction = extract(
                    client,
                    articles,
                    base_uri=settings.llm_base_uri,
                    model=settings.llm_model,
                    max_chars=settings.summary_max_chars,
                    timeout=settings.llm_timeout,
                    max_entities=settings.max_entities,
                    max_relations=settings.max_relations,
                )
                with engine.begin() as conn:
                    if not lock_cluster(conn, cluster_id, seen):
                        logger.info("cluster %d changed during extraction, skipped", cluster_id)
                        continue
                    dropped = write_graph(
                        conn, cluster_id, seen, extraction, resolve(conn, extraction.entities)
                    )
            except Exception:
                logger.exception("graph extraction failed for cluster %d", cluster_id)
                failed += 1
                continue
            if dropped:
                logger.info("cluster %d: dropped %d dangling relations", cluster_id, dropped)
        logger.info("built %d cluster graphs, %d failed", len(due) - failed, failed)
    sys.exit(1 if sync_failed or failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTEST services/news-graph-builder -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/news-graph-builder
git commit -m "feat(graph-builder): sync companies, then summarize and graph every due cluster

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Documentation and full verification

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Update `AGENTS.md`**

- In the Commands block, replace the news-clusterer compose line with:
  ```bash
  docker compose -f compose.dev.yaml up news-clusterer
  NEWS_GRAPH_BUILDER_LLM_BASE_URI=http://100.bbb.ccc.ddd:8001/v1 NEWS_GRAPH_BUILDER_LLM_MODEL=<model> NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY=<key> NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY=<key> docker compose -f compose.dev.yaml up news-graph-builder
  ```
  and add after the pytest lines:
  ```bash
  # tests TRUNCATE tables: point them at a separate database, never at `news`
  docker compose -f compose.dev.yaml exec postgres createdb -U ktb news_test
  ```
- In the member table, add after the news-clusterer row:
  `| services/news-graph-builder | service | cron: main() runs once and exits | ktb-core |`
- Replace the "Services communicate only through datastores." bullet with:
  `- **Services communicate only through datastores.** news-preprocessor writes articles to PostgreSQL, news-clusterer reads them and writes clusters / article_clusters, news-graph-builder reads those and writes cluster_summaries plus the knowledge graph (companies, company_aliases, entities, cluster_entities, relations; design: docs/superpowers/specs/2026-09-24-news-graph-builder-design.md), and portfolio-builder reads them all. There are no direct service-to-service calls.`
- In the settings bullet, add `NEWS_GRAPH_BUILDER_` to the list of env prefixes.
- Add a bullet under Architecture:
  `- **news-graph-builder needs a Kiwoom app key and a DART key.** The Kiwoom key can place trades: prefer a paper-trading (모의투자) key via NEWS_GRAPH_BUILDER_KIWOOM_BASE_URI, never commit it, and register the task's outbound IP with Kiwoom.`

- [ ] **Step 2: Run the full verification**

```bash
uv sync --all-packages --locked --group migrations
uv run ruff check .
uv run ruff format --check .
uv run tach check
uv run tach check-external -e packages/market-analyzer,services
KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news_test uv run pytest
for pkg in news-preprocessor news-clusterer news-graph-builder portfolio-builder; do uv export --package "$pkg" --no-dev --no-emit-workspace --format requirements-txt -o "docker/requirements/${pkg}.txt" > /dev/null; done; git diff --exit-code -- docker/requirements/
docker build -f docker/news-clusterer.Dockerfile -t news-clusterer:dev .
docker build -f docker/news-graph-builder.Dockerfile -t news-graph-builder:dev .
docker run --rm news-graph-builder:dev python -c "import news_graph_builder.__main__, opendartreader.dart_list"
```
Expected: every command exits 0, and pytest shows no failures and no skips for the Postgres tests.

Then run deptry, which CI treats as advisory (`continue-on-error`):
```bash
uv run deptry services/news-graph-builder/src --config services/news-graph-builder/pyproject.toml
```
Expected: at most the same findings the existing services already have (`pydantic` imported through `pydantic-settings`, `psycopg` used only as the SQLAlchemy driver). Fix anything else.

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs: document news-graph-builder in AGENTS.md

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Hand over the live checks**

The remaining spec §9 items need real keys, which this plan never reads: the Kiwoom code format, whether the paper-trading domain serves `ka10099`, and whether vLLM accepts the nested schema. Report to the user that the first real run settles them. It logs `synced companies: kiwoom=… dart=… joined=…`, and a `joined` of 0 fails the sync on purpose.
