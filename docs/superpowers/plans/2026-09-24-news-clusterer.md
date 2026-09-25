# news-clusterer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `news-clusterer` FastAPI skeleton into a cron job that runs DBSCAN over every embedded article, stores the clusters in PostgreSQL with stable ids, and summarizes changed clusters with a vLLM chat model.

**Architecture:** `main()` loads all embeddings, runs our own numpy DBSCAN, logs the clustering cost, matches the new clusters to last run's (read from `article_clusters`) by largest overlap, writes the difference in one transaction, then asks the LLM for a Korean title and summary for each cluster whose membership changed.

**Tech Stack:** Python 3.13 (CI also 3.14), uv workspace, numpy, SQLAlchemy 2 + psycopg 3 + pgvector, httpx, pydantic-settings, Alembic, pytest; scikit-learn as a test-only reference.

**Spec:** `docs/superpowers/specs/2026-09-23-news-clusterer-design.md`

## Global Constraints

- Run every command from the repo root. Tests: `uv run pytest ...`; lint: `uv run ruff check .` and `uv run ruff format --check .` (line length 100).
- PostgreSQL tests need `KTB_TEST_POSTGRES_DSN` and a migrated database; without it they are skipped. Local setup:
  ```bash
  docker compose -f compose.dev.yaml up -d postgres
  export KTB_TEST_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@localhost:5432/news
  KTB_POSTGRES_DSN=$KTB_TEST_POSTGRES_DSN uv run alembic upgrade head
  ```
- Settings env prefix `NEWS_CLUSTERER_`. `postgres_dsn`, `llm_base_uri`, `llm_model` are required with no default. No LLM host address may be committed anywhere in the repo.
- scikit-learn lives only in the root `dev` group and must never appear in `docker/requirements/news-clusterer.txt`.
- No comments or docstrings that restate names; comments only for a non-obvious why. One module per top-level function; no thin wrappers.
- Commit titles use `feat` / `fix` / `refactor` / `chore` / `docs` / `style` / `test` / `build` prefixes. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- After changing dependencies: `uv lock`, then
  `uv export --package news-clusterer --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/news-clusterer.txt` (exactly this path).

---

### Task 1: Remove the HTTP surface; new settings and dependencies

**Files:**
- Delete: `services/news-clusterer/src/news_clusterer/app.py`, `services/news-clusterer/tests/test_app.py`
- Modify: `services/news-clusterer/pyproject.toml`, `services/news-clusterer/src/news_clusterer/settings.py`, `services/news-clusterer/src/news_clusterer/__main__.py`, `docker/news-clusterer.Dockerfile`, `pyproject.toml` (root), `uv.lock`, `docker/requirements/news-clusterer.txt`
- Test: `services/news-clusterer/tests/test_settings.py` (rewrite)

**Interfaces:**
- Produces: `news_clusterer.settings.Settings` with fields `log_level: str = "INFO"`, `postgres_dsn: str`, `llm_base_uri: str`, `llm_model: str`, `eps: float = 0.2`, `min_samples: int = 3`, `summary_max_chars: int = 24000`, `llm_timeout: float = 120`.

- [ ] **Step 1: Rewrite the settings test**

`services/news-clusterer/tests/test_settings.py`:

```python
import pytest
from news_clusterer.settings import Settings
from pydantic import ValidationError

REQUIRED = {
    "NEWS_CLUSTERER_POSTGRES_DSN": "postgresql+psycopg://ktb:ktb@localhost:5432/news",
    "NEWS_CLUSTERER_LLM_BASE_URI": "http://llm.test/v1",
    "NEWS_CLUSTERER_LLM_MODEL": "test-model",
}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = Settings()

    assert settings.llm_base_uri == "http://llm.test/v1"
    assert settings.llm_model == "test-model"
    assert settings.eps == 0.2
    assert settings.min_samples == 3
    assert settings.summary_max_chars == 24000
    assert settings.llm_timeout == 120


def test_clustering_parameters_come_from_the_environment(required_env, monkeypatch):
    monkeypatch.setenv("NEWS_CLUSTERER_EPS", "0.15")
    monkeypatch.setenv("NEWS_CLUSTERER_MIN_SAMPLES", "5")

    settings = Settings()

    assert settings.eps == 0.15
    assert settings.min_samples == 5


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NEWS_CLUSTERER_EPS", "0"),
        ("NEWS_CLUSTERER_EPS", "2.5"),
        ("NEWS_CLUSTERER_MIN_SAMPLES", "0"),
        ("NEWS_CLUSTERER_SUMMARY_MAX_CHARS", "0"),
    ],
)
def test_out_of_range_values_raise(required_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/news-clusterer/tests/test_settings.py -v`
Expected: FAIL (`Settings` has no `llm_base_uri`; `NEWS_CLUSTERER_EPS=0` does not raise).

- [ ] **Step 3: Replace settings**

`services/news-clusterer/src/news_clusterer/settings.py`:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_CLUSTERER_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
    llm_base_uri: str
    llm_model: str
    # Cosine distance lies in [0, 2].
    eps: float = Field(default=0.2, gt=0, le=2)
    min_samples: int = Field(default=3, gt=0)
    summary_max_chars: int = Field(default=24000, gt=0)
    llm_timeout: float = Field(default=120, gt=0)
```

- [ ] **Step 4: Delete the app and make `main()` a plain job**

```bash
git rm services/news-clusterer/src/news_clusterer/app.py services/news-clusterer/tests/test_app.py
```

`services/news-clusterer/src/news_clusterer/__main__.py` (Task 7 replaces this body):

```python
import logging

from ktb_core.logging import setup_logging

from news_clusterer.settings import Settings

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-clusterer started")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Swap dependencies**

`services/news-clusterer/pyproject.toml` — replace the `dependencies` list and the description:

```toml
description = "Cron-triggered news clustering and summarization"
requires-python = ">=3.13,<3.15"
dependencies = [
    "ktb-core",
    "pydantic-settings>=2.7",
    "sqlalchemy>=2.0.54",
    "psycopg[binary]>=3.3.6",
    "pgvector>=0.5.0",
    "httpx>=0.28",
    "numpy>=2.5",
]
```

Root `pyproject.toml` — add scikit-learn to the `dev` group:

```toml
dev = [
    "pytest>=8.0",
    "httpx>=0.28",
    "ruff>=0.14",
    "ty==0.0.82",
    "scikit-learn>=1.7",
]
```

`docker/news-clusterer.Dockerfile` — delete the line `EXPOSE 8000`.

Then:

```bash
uv lock
uv sync --all-packages --group migrations
uv export --package news-clusterer --no-dev --no-emit-workspace --format requirements-txt -o docker/requirements/news-clusterer.txt
grep -ci -E "scikit|fastapi|uvicorn" docker/requirements/news-clusterer.txt
```

Expected: the last command prints `0`.

- [ ] **Step 6: Run the tests and lint**

Run: `uv run pytest services/news-clusterer -v && uv run ruff check . && uv run ruff format --check .`
Expected: all settings tests PASS; lint clean.

- [ ] **Step 7: Commit**

```bash
git add -A services/news-clusterer pyproject.toml uv.lock docker/news-clusterer.Dockerfile docker/requirements/news-clusterer.txt
git commit -m "refactor(clusterer): drop the HTTP surface; add clustering and LLM settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: DBSCAN

**Files:**
- Create: `services/news-clusterer/src/news_clusterer/dbscan.py`
- Test: `services/news-clusterer/tests/test_dbscan.py`

**Interfaces:**
- Produces: `dbscan(vectors: np.ndarray, eps: float, min_samples: int) -> np.ndarray` — `vectors` is `float32[n, d]` with unit-length rows; returns `int64[n]`, `-1` for noise, clusters `0..k-1`.

- [ ] **Step 1: Write the failing test against sklearn**

`services/news-clusterer/tests/test_dbscan.py`:

```python
import numpy as np
import pytest
from news_clusterer.dbscan import dbscan
from sklearn.cluster import DBSCAN


def unit(rows: np.ndarray) -> np.ndarray:
    return (rows / np.linalg.norm(rows, axis=1, keepdims=True)).astype(np.float32)


def blobs() -> np.ndarray:
    # 64-d blobs: within a blob cosine distance stays below ~0.02, across blobs and to the
    # scattered points it stays above ~0.4, so no eps tested here sits near a real distance.
    rng = np.random.default_rng(7)
    centers = rng.normal(size=(5, 64))
    sizes = [12, 8, 5, 2, 1]
    points = [center + rng.normal(scale=0.01, size=(size, 64)) for center, size in zip(centers, sizes)]
    scattered = rng.normal(size=(10, 64))
    vectors = unit(np.vstack([*points, scattered]))
    return vectors[rng.permutation(len(vectors))]


def arc_chains() -> np.ndarray:
    # Points on the unit circle 0.1 rad apart: with eps = 1 - cos(0.15) only direct
    # neighbours connect, so chain ends are border points rather than core points.
    angles = np.concatenate([np.arange(6) * 0.1, np.pi + np.arange(4) * 0.1, [np.pi / 2]])
    return unit(np.column_stack([np.cos(angles), np.sin(angles)]))


def assert_same_partition(ours: np.ndarray, reference: np.ndarray) -> None:
    assert ((ours == -1) == (reference == -1)).all()
    pairs = set(zip(ours.tolist(), reference.tolist()))
    assert len(pairs) == len(set(ours.tolist())) == len(set(reference.tolist()))


@pytest.mark.parametrize(
    ("make", "eps", "min_samples"),
    [
        (blobs, 0.1, 3),
        (blobs, 0.1, 6),
        (blobs, 0.1, 1),
        (blobs, 1e-6, 2),
        (arc_chains, 1 - np.cos(0.15), 3),
        (arc_chains, 1 - np.cos(0.15), 2),
    ],
)
def test_matches_sklearn(make, eps, min_samples):
    vectors = make()

    ours = dbscan(vectors, eps, min_samples)
    reference = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit(vectors).labels_

    assert ours.dtype == np.int64
    assert_same_partition(ours, reference)


def test_labels_are_consecutive_from_zero():
    labels = dbscan(blobs(), 0.1, 3)

    clusters = sorted(set(labels.tolist()) - {-1})
    assert clusters == list(range(len(clusters)))


def test_min_samples_one_leaves_no_noise_even_for_a_tiny_eps():
    labels = dbscan(blobs(), 1e-9, 1)

    assert (labels != -1).all()


def test_rows_beyond_one_block_are_clustered(monkeypatch):
    import news_clusterer.dbscan as module

    monkeypatch.setattr(module, "BLOCK_ROWS", 4)
    vectors = blobs()

    assert_same_partition(
        module.dbscan(vectors, 0.1, 3),
        DBSCAN(eps=0.1, min_samples=3, metric="cosine").fit(vectors).labels_,
    )


def test_empty_input():
    labels = dbscan(np.empty((0, 64), dtype=np.float32), 0.1, 3)

    assert labels.shape == (0,)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/news-clusterer/tests/test_dbscan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'news_clusterer.dbscan'`.

- [ ] **Step 3: Implement**

`services/news-clusterer/src/news_clusterer/dbscan.py`:

```python
import numpy as np

BLOCK_ROWS = 1024


def dbscan(vectors: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    n = len(vectors)
    neighbors: list[np.ndarray] = []
    # ponytail: O(n²) distances per run, computed in row blocks so memory stays BLOCK_ROWS × n;
    # switch to incremental DBSCAN when the "clustering cost" log shows a run no longer fits.
    for start in range(0, n, BLOCK_ROWS):
        distances = 1 - vectors[start : start + BLOCK_ROWS] @ vectors.T
        # Rounding can put a point's distance to itself above a tiny eps; it is always 0.
        np.fill_diagonal(distances[:, start:], 0)
        neighbors.extend(np.flatnonzero(row <= eps) for row in distances)

    is_core = [len(points) >= min_samples for points in neighbors]
    labels = np.full(n, -1, dtype=np.int64)
    cluster = 0
    for seed in range(n):
        if labels[seed] != -1 or not is_core[seed]:
            continue
        labels[seed] = cluster
        stack = [seed]
        while stack:
            for neighbor in neighbors[stack.pop()]:
                if labels[neighbor] == -1:
                    labels[neighbor] = cluster
                    if is_core[neighbor]:
                        stack.append(neighbor)
        cluster += 1
    return labels
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/news-clusterer/tests/test_dbscan.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/news-clusterer/src/news_clusterer/dbscan.py services/news-clusterer/tests/test_dbscan.py
git commit -m "feat(clusterer): add DBSCAN checked against sklearn

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Match new clusters to old ones

**Files:**
- Create: `services/news-clusterer/src/news_clusterer/match.py`
- Test: `services/news-clusterer/tests/test_match.py`

**Interfaces:**
- Produces: `match(new: dict[int, set[int]], old: dict[int, set[int]]) -> tuple[dict[int, int | None], set[int]]`. `new` maps DBSCAN label → article ids; `old` maps cluster id → article ids. Returns (label → matched old cluster id or `None`, old cluster ids left unmatched).

- [ ] **Step 1: Write the failing test**

`services/news-clusterer/tests/test_match.py`:

```python
from news_clusterer.match import match


def test_unchanged_clusters_keep_their_ids():
    matches, unmatched = match({0: {1, 2}, 1: {3, 4}}, {10: {3, 4}, 20: {1, 2}})

    assert matches == {0: 20, 1: 10}
    assert unmatched == set()


def test_a_grown_cluster_keeps_its_id():
    matches, unmatched = match({0: {1, 2, 3}}, {10: {1, 2}})

    assert matches == {0: 10}
    assert unmatched == set()


def test_a_split_keeps_the_id_on_the_larger_part():
    matches, unmatched = match({0: {1}, 1: {2, 3}}, {10: {1, 2, 3}})

    assert matches == {0: None, 1: 10}
    assert unmatched == set()


def test_a_merge_keeps_the_id_with_the_larger_overlap():
    matches, unmatched = match({0: {1, 2, 3}}, {10: {1}, 20: {2, 3}})

    assert matches == {0: 20}
    assert unmatched == {10}


def test_equal_overlaps_prefer_the_lower_old_id_then_the_lower_label():
    matches, unmatched = match({0: {1, 2}, 1: {3, 4}}, {10: {1, 3}, 20: {2, 4}})

    assert matches == {0: 10, 1: 20}
    assert unmatched == set()


def test_first_run_and_vanished_clusters():
    assert match({0: {1}}, {}) == ({0: None}, set())
    assert match({}, {10: {1}}) == ({}, {10})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/news-clusterer/tests/test_match.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'news_clusterer.match'`.

- [ ] **Step 3: Implement**

`services/news-clusterer/src/news_clusterer/match.py`:

```python
def match(
    new: dict[int, set[int]], old: dict[int, set[int]]
) -> tuple[dict[int, int | None], set[int]]:
    # ponytail: compares every (new, old) pair; index articles by old cluster if this gets slow.
    pairs = sorted(
        (-len(members & previous), old_id, label)
        for label, members in new.items()
        for old_id, previous in old.items()
        if members & previous
    )
    matches: dict[int, int | None] = dict.fromkeys(new)
    taken: set[int] = set()
    for _, old_id, label in pairs:
        if matches[label] is None and old_id not in taken:
            matches[label] = old_id
            taken.add(old_id)
    return matches, set(old) - taken
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/news-clusterer/tests/test_match.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/news-clusterer/src/news_clusterer/match.py services/news-clusterer/tests/test_match.py
git commit -m "feat(clusterer): match new clusters to previous ones by overlap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Migration 0002 and the clusterer table mirror

**Files:**
- Create: `infrastructure/postgres/migrations/versions/0002_create_clusters.py`
- Create: `services/news-clusterer/src/news_clusterer/storage.py` (tables only in this task)
- Modify: `infrastructure/postgres/tests/test_migrations.py`, `services/news-preprocessor/tests/conftest.py`

**Interfaces:**
- Produces: `news_clusterer.storage.metadata`, `articles`, `clusters`, `article_clusters` SQLAlchemy tables.

- [ ] **Step 1: Make the drift check cover both services**

In `infrastructure/postgres/tests/test_migrations.py`, replace `test_service_table_matches_the_migrated_schema` with:

```python
@pytest.mark.parametrize(
    ("module", "owned_tables"),
    [
        ("news_preprocessor.storage", {"articles"}),
        ("news_clusterer.storage", {"clusters", "article_clusters"}),
    ],
)
def test_service_tables_match_the_migrated_schema(
    pg_dsn, pg_engine, monkeypatch, module, owned_tables
):
    metadata = importlib.import_module(module).metadata

    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")

    def only_owned_tables(name, type_, parent_names):
        return name in owned_tables if type_ == "table" else True

    with pg_engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "include_name": only_owned_tables}
        )
        assert compare_metadata(context, metadata) == []
```

Add `import importlib` and `import pytest` to the file's imports (keep ruff's isort order). Also add after the existing downgrade/upgrade test:

```python
def test_downgrade_removes_the_cluster_tables(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()

    command.downgrade(config, "0001")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('clusters')")).scalar() is None

    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('clusters')")).scalar() is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest infrastructure -v`
Expected: the `news_clusterer.storage` case FAILS with `ModuleNotFoundError`, and `test_downgrade_removes_the_cluster_tables` FAILS (no revision `0002`).

- [ ] **Step 3: Write the migration**

`infrastructure/postgres/migrations/versions/0002_create_clusters.py`:

```python
"""create clusters

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clusters",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "article_clusters",
        sa.Column("article_id", sa.BigInteger, sa.ForeignKey("articles.id"), primary_key=True),
        sa.Column(
            "cluster_id",
            sa.BigInteger,
            sa.ForeignKey("clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("article_clusters_cluster_id_idx", "article_clusters", ["cluster_id"])


def downgrade() -> None:
    op.drop_table("article_clusters")
    op.drop_table("clusters")
```

- [ ] **Step 4: Mirror the tables in the clusterer**

`services/news-clusterer/src/news_clusterer/storage.py`:

```python
import sqlalchemy as sa
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
from pgvector.sqlalchemy import VECTOR

# Mirrors infrastructure/postgres/migrations for queries only; the migrations own the schema.
metadata = sa.MetaData()

# Only the article columns this service reads; news-preprocessor owns the full table.
articles = sa.Table(
    "articles",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("embedding", VECTOR(EMBEDDING_DIMENSIONS), nullable=True),
)

clusters = sa.Table(
    "clusters",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("title", sa.Text, nullable=True),
    sa.Column("summary", sa.Text, nullable=True),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True),
)

article_clusters = sa.Table(
    "article_clusters",
    metadata,
    sa.Column("article_id", sa.BigInteger, sa.ForeignKey("articles.id"), primary_key=True),
    sa.Column(
        "cluster_id",
        sa.BigInteger,
        sa.ForeignKey("clusters.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Index("article_clusters_cluster_id_idx", "cluster_id"),
)
```

- [ ] **Step 5: Keep the preprocessor fixture working**

`article_clusters` now references `articles`, so a plain `TRUNCATE articles` is rejected. In `services/news-preprocessor/tests/conftest.py` change:

```python
        conn.execute(sa.text("TRUNCATE articles RESTART IDENTITY"))
```

to:

```python
        conn.execute(sa.text("TRUNCATE articles RESTART IDENTITY CASCADE"))
```

- [ ] **Step 6: Migrate and run the tests**

```bash
KTB_POSTGRES_DSN=$KTB_TEST_POSTGRES_DSN uv run alembic upgrade head
uv run pytest infrastructure services/news-preprocessor -v
```

Expected: all PASS, including both drift cases (not skipped — confirm `KTB_TEST_POSTGRES_DSN` is set).

- [ ] **Step 7: Commit**

```bash
git add infrastructure services/news-clusterer/src/news_clusterer/storage.py services/news-preprocessor/tests/conftest.py
git commit -m "feat: add clusters and article_clusters tables

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Storage queries

**Files:**
- Modify: `services/news-clusterer/src/news_clusterer/storage.py` (append functions)
- Create: `services/news-clusterer/tests/conftest.py`
- Test: `services/news-clusterer/tests/test_storage.py`

**Interfaces:**
- Consumes: tables from Task 4.
- Produces:
  - `load_embeddings(conn: sa.Connection) -> tuple[list[int], np.ndarray]` — article ids ordered by id, and a `float32[n, EMBEDDING_DIMENSIONS]` matrix.
  - `load_assignment(conn: sa.Connection) -> dict[int, set[int]]` — cluster id → article ids.
  - `write_clusters(conn: sa.Connection, new: dict[int, set[int]], old: dict[int, set[int]], matches: dict[int, int | None], unmatched: set[int]) -> None`
  - `clusters_needing_summary(conn: sa.Connection) -> list[int]` — ordered by id.
  - `cluster_articles(conn: sa.Connection, cluster_id: int) -> list[tuple[str, str]]` — (title, body), newest `published_at` first.
  - `set_summary(conn: sa.Connection, cluster_id: int, title: str, summary: str) -> None`
  - Test fixtures `engine` and `add_article(conn, embedding=None, published_at=...) -> int` (in `conftest.py`).

- [ ] **Step 1: Add test fixtures**

`services/news-clusterer/tests/conftest.py`:

```python
from datetime import UTC, datetime
from itertools import count

import pytest
import sqlalchemy as sa

_external_ids = count()


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text("TRUNCATE article_clusters, clusters, articles RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def engine(pg_engine):
    # The code under test commits, so empty the tables instead of rolling back.
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)


def add_article(
    conn: sa.Connection,
    embedding: list[float] | None = None,
    published_at: datetime = datetime(2026, 9, 24, tzinfo=UTC),
    title: str = "title",
    body: str = "body",
) -> int:
    return conn.execute(
        sa.text(
            "INSERT INTO articles"
            " (source, external_id, url, title, body, published_at, raw_payload, embedding)"
            " VALUES ('test', :external_id, 'https://example.com', :title, :body,"
            " :published_at, '', CAST(:embedding AS vector))"
            " RETURNING id"
        ),
        {
            "external_id": str(next(_external_ids)),
            "title": title,
            "body": body,
            "published_at": published_at,
            "embedding": None
            if embedding is None
            else "[" + ",".join(map(str, embedding)) + "]",
        },
    ).scalar_one()


@pytest.fixture
def article():
    return add_article
```

- [ ] **Step 2: Write the failing storage tests**

`services/news-clusterer/tests/test_storage.py`:

```python
from datetime import UTC, datetime

import numpy as np
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
from news_clusterer.match import match
from news_clusterer.storage import (
    cluster_articles,
    clusters_needing_summary,
    load_assignment,
    load_embeddings,
    set_summary,
    write_clusters,
)


def basis(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[index] = 1.0
    return vector


def apply(engine, new):
    with engine.begin() as conn:
        old = load_assignment(conn)
        matches, unmatched = match(new, old)
        write_clusters(conn, new, old, matches, unmatched)


def test_load_embeddings_skips_articles_without_one(engine, article):
    with engine.begin() as conn:
        first = article(conn, basis(0))
        article(conn)
        third = article(conn, basis(1))

    with engine.connect() as conn:
        ids, vectors = load_embeddings(conn)

    assert ids == [first, third]
    assert vectors.dtype == np.float32
    assert vectors.shape == (2, EMBEDDING_DIMENSIONS)
    assert vectors[1, 1] == 1.0


def test_load_embeddings_on_an_empty_table(engine):
    with engine.connect() as conn:
        ids, vectors = load_embeddings(conn)

    assert ids == []
    assert vectors.shape == (0, EMBEDDING_DIMENSIONS)


def test_first_write_creates_clusters_that_need_summaries(engine, article):
    with engine.begin() as conn:
        a, b, c = (article(conn) for _ in range(3))

    apply(engine, {0: {a, b}, 1: {c}})

    with engine.connect() as conn:
        assignment = load_assignment(conn)
        pending = clusters_needing_summary(conn)
    assert sorted(assignment.values(), key=min) == [{a, b}, {c}]
    assert pending == sorted(assignment)


def test_rewrite_keeps_ids_moves_members_and_drops_noise(engine, article):
    with engine.begin() as conn:
        a, b, c, d, e = (article(conn) for _ in range(5))
    apply(engine, {0: {a, b}, 1: {c, d}})
    with engine.connect() as conn:
        before = load_assignment(conn)
    kept = next(cluster_id for cluster_id, members in before.items() if a in members)
    dropped = next(cluster_id for cluster_id in before if cluster_id != kept)
    with engine.begin() as conn:
        for cluster_id in before:
            set_summary(conn, cluster_id, "t", "s")

    # c and d become noise, e joins a's cluster.
    apply(engine, {0: {a, b, e}})

    with engine.connect() as conn:
        assert load_assignment(conn) == {kept: {a, b, e}}
        assert clusters_needing_summary(conn) == [kept]
        remaining = conn.exec_driver_sql("SELECT id FROM clusters").scalars().all()
    assert remaining == [kept]
    assert dropped not in remaining


def test_an_unchanged_cluster_stays_summarized(engine, article):
    with engine.begin() as conn:
        a, b = article(conn), article(conn)
    apply(engine, {0: {a, b}})
    with engine.connect() as conn:
        (cluster_id,) = load_assignment(conn)
    with engine.begin() as conn:
        set_summary(conn, cluster_id, "t", "s")

    apply(engine, {5: {a, b}})

    with engine.connect() as conn:
        assert clusters_needing_summary(conn) == []


def test_cluster_articles_are_newest_first(engine, article):
    with engine.begin() as conn:
        old = article(conn, published_at=datetime(2026, 9, 1, tzinfo=UTC), title="old")
        new = article(conn, published_at=datetime(2026, 9, 2, tzinfo=UTC), title="new")
    apply(engine, {0: {old, new}})

    with engine.connect() as conn:
        (cluster_id,) = load_assignment(conn)
        rows = cluster_articles(conn, cluster_id)

    assert rows == [("new", "body"), ("old", "body")]


def test_set_summary_stores_title_and_summary(engine, article):
    with engine.begin() as conn:
        a = article(conn)
    apply(engine, {0: {a}})
    with engine.connect() as conn:
        (cluster_id,) = load_assignment(conn)

    with engine.begin() as conn:
        set_summary(conn, cluster_id, "제목", "요약")

    with engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT title, summary FROM clusters").one()
    assert tuple(row) == ("제목", "요약")
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest services/news-clusterer/tests/test_storage.py -v`
Expected: FAIL with `ImportError: cannot import name 'cluster_articles'` (confirm the tests are collected, not skipped).

- [ ] **Step 4: Implement the queries**

Append to `services/news-clusterer/src/news_clusterer/storage.py` (merge the new imports into the top of the file):

```python
from collections.abc import Iterable

import numpy as np
from sqlalchemy.dialects.postgresql import insert


def load_embeddings(conn: sa.Connection) -> tuple[list[int], np.ndarray]:
    rows = conn.execute(
        sa.select(articles.c.id, articles.c.embedding)
        .where(articles.c.embedding.is_not(None))
        .order_by(articles.c.id)
    ).all()
    vectors = np.array([row.embedding for row in rows], dtype=np.float32)
    return [row.id for row in rows], vectors.reshape(len(rows), EMBEDDING_DIMENSIONS)


def load_assignment(conn: sa.Connection) -> dict[int, set[int]]:
    assignment: dict[int, set[int]] = {}
    for article_id, cluster_id in conn.execute(
        sa.select(article_clusters.c.article_id, article_clusters.c.cluster_id)
    ):
        assignment.setdefault(cluster_id, set()).add(article_id)
    return assignment


def _by_article(assignment: Iterable[tuple[int, set[int]]]) -> dict[int, int]:
    return {article_id: cluster_id for cluster_id, members in assignment for article_id in members}


def write_clusters(
    conn: sa.Connection,
    new: dict[int, set[int]],
    old: dict[int, set[int]],
    matches: dict[int, int | None],
    unmatched: set[int],
) -> None:
    cluster_ids: dict[int, int] = {}
    for label, old_id in matches.items():
        if old_id is None:
            cluster_ids[label] = conn.execute(
                insert(clusters).returning(clusters.c.id)
            ).scalar_one()
        else:
            cluster_ids[label] = old_id
            if new[label] != old[old_id]:
                conn.execute(
                    sa.update(clusters)
                    .where(clusters.c.id == old_id)
                    .values(updated_at=sa.func.now())
                )

    previous = _by_article(old.items())
    desired = _by_article((cluster_ids[label], members) for label, members in new.items())
    changed = [
        {"article_id": article_id, "cluster_id": cluster_id}
        for article_id, cluster_id in desired.items()
        if previous.get(article_id) != cluster_id
    ]
    if changed:
        statement = insert(article_clusters).values(changed)
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=[article_clusters.c.article_id],
                set_={"cluster_id": statement.excluded.cluster_id},
            )
        )
    noise = previous.keys() - desired.keys()
    if noise:
        conn.execute(sa.delete(article_clusters).where(article_clusters.c.article_id.in_(noise)))
    if unmatched:
        conn.execute(sa.delete(clusters).where(clusters.c.id.in_(unmatched)))


def clusters_needing_summary(conn: sa.Connection) -> list[int]:
    query = (
        sa.select(clusters.c.id)
        .where(
            sa.or_(
                clusters.c.summarized_at.is_(None),
                clusters.c.summarized_at < clusters.c.updated_at,
            )
        )
        .order_by(clusters.c.id)
    )
    return list(conn.execute(query).scalars())


def cluster_articles(conn: sa.Connection, cluster_id: int) -> list[tuple[str, str]]:
    query = (
        sa.select(articles.c.title, articles.c.body)
        .join(article_clusters, article_clusters.c.article_id == articles.c.id)
        .where(article_clusters.c.cluster_id == cluster_id)
        .order_by(articles.c.published_at.desc(), articles.c.id.desc())
    )
    return [(row.title, row.body) for row in conn.execute(query)]


def set_summary(conn: sa.Connection, cluster_id: int, title: str, summary: str) -> None:
    conn.execute(
        sa.update(clusters)
        .where(clusters.c.id == cluster_id)
        .values(title=title, summary=summary, summarized_at=sa.func.now())
    )
```

`_by_article` has two callers inside `write_clusters`, so it stays a module-private helper.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest services/news-clusterer/tests/test_storage.py infrastructure -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add services/news-clusterer
git commit -m "feat(clusterer): read embeddings and write clusters to PostgreSQL

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Summarize a cluster with the LLM

**Files:**
- Create: `services/news-clusterer/src/news_clusterer/summarize.py`
- Test: `services/news-clusterer/tests/test_summarize.py`

**Interfaces:**
- Produces: `summarize(client: httpx.Client, articles: Sequence[tuple[str, str]], *, base_uri: str, model: str, max_chars: int, timeout: float) -> tuple[str, str]` — returns `(title, summary)`; raises `httpx.HTTPError` or `ValueError` on failure. `articles` is (title, body), newest first.

vLLM request shape (verified in the vLLM structured-outputs docs): `response_format={"type": "json_schema", "json_schema": {"name": ..., "schema": {...}}}`; the reply text is `choices[0].message.content`.

- [ ] **Step 1: Write the failing test**

`services/news-clusterer/tests/test_summarize.py`:

```python
import json

import httpx
import pytest
from news_clusterer.summarize import summarize

BASE_URI = "http://llm.test/v1"


def client_replying(content: str, status: int = 200, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def call(client, articles, max_chars=1000):
    return summarize(
        client, articles, base_uri=BASE_URI, model="test-model", max_chars=max_chars, timeout=5
    )


def test_returns_title_and_summary():
    seen = []
    client = client_replying(json.dumps({"title": "금리 인상", "summary": "요약"}), seen=seen)

    assert call(client, [("기사", "본문")]) == ("금리 인상", "요약")

    body = json.loads(seen[0].content)
    assert str(seen[0].url) == f"{BASE_URI}/chat/completions"
    assert body["model"] == "test-model"
    assert body["response_format"]["type"] == "json_schema"
    assert "기사\n\n본문" in body["messages"][-1]["content"]


def test_budget_keeps_the_newest_article_and_stops_before_overflow():
    seen = []
    client = client_replying(json.dumps({"title": "t", "summary": "s"}), seen=seen)

    call(client, [("new", "x" * 50), ("old", "y" * 50)], max_chars=80)

    prompt = json.loads(seen[0].content)["messages"][-1]["content"]
    assert "new\n\n" in prompt
    assert "old" not in prompt


def test_the_newest_article_is_truncated_to_the_budget():
    seen = []
    client = client_replying(json.dumps({"title": "t", "summary": "s"}), seen=seen)

    call(client, [("new", "x" * 500)], max_chars=20)

    prompt = json.loads(seen[0].content)["messages"][-1]["content"]
    # "new\n\n" takes 5 of the 20 characters.
    assert "x" * 15 in prompt
    assert "x" * 16 not in prompt


@pytest.mark.parametrize(
    "content", ["not json", json.dumps({"title": "t"}), json.dumps({"title": 1, "summary": "s"})]
)
def test_malformed_replies_raise_value_error(content):
    with pytest.raises(ValueError):
        call(client_replying(content), [("a", "b")])


def test_http_errors_raise():
    with pytest.raises(httpx.HTTPStatusError):
        call(client_replying("", status=500), [("a", "b")])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/news-clusterer/tests/test_summarize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'news_clusterer.summarize'`.

- [ ] **Step 3: Implement**

`services/news-clusterer/src/news_clusterer/summarize.py`:

```python
import json
from collections.abc import Sequence

import httpx

SYSTEM_PROMPT = (
    "당신은 경제 뉴스 편집자입니다. 같은 사건을 다룬 기사들이 주어집니다. "
    "사건을 대표하는 한 줄 제목과 3~5문장 요약을 한국어로 작성하세요."
)

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "cluster_summary",
        "schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["title", "summary"],
            "additionalProperties": False,
        },
    },
}


def summarize(
    client: httpx.Client,
    articles: Sequence[tuple[str, str]],
    *,
    base_uri: str,
    model: str,
    max_chars: int,
    timeout: float,
) -> tuple[str, str]:
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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(blocks)},
            ],
            "response_format": RESPONSE_FORMAT,
        },
        timeout=timeout,
    )
    content = response.raise_for_status().json()["choices"][0]["message"]["content"]
    try:
        reply = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"LLM reply is not JSON: {content!r}") from error
    if not (isinstance(reply.get("title"), str) and isinstance(reply.get("summary"), str)):
        raise ValueError(f"LLM reply lacks a string title and summary: {content!r}")
    return reply["title"], reply["summary"]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest services/news-clusterer/tests/test_summarize.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/news-clusterer/src/news_clusterer/summarize.py services/news-clusterer/tests/test_summarize.py
git commit -m "feat(clusterer): summarize a cluster with the vLLM chat endpoint

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The run — `main()` with the clustering cost log

**Files:**
- Modify: `services/news-clusterer/src/news_clusterer/__main__.py`
- Test: `services/news-clusterer/tests/test_main.py`

**Interfaces:**
- Consumes: `Settings` (Task 1), `dbscan` (Task 2), `match` (Task 3), storage functions (Task 5), `summarize` (Task 6).
- Produces: console script `news-clusterer` → `main()`, exits 0 or 1.

- [ ] **Step 1: Write the failing end-to-end test**

`services/news-clusterer/tests/test_main.py`:

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
    monkeypatch.setenv("NEWS_CLUSTERER_LLM_BASE_URI", "http://llm.test/v1")
    monkeypatch.setenv("NEWS_CLUSTERER_LLM_MODEL", "test-model")
    # setup_logging replaces the root handlers, which would detach caplog.
    monkeypatch.setattr(entry, "setup_logging", lambda level: None)


@pytest.fixture
def summaries(monkeypatch):
    calls = []

    def fake(client, articles, **kwargs):
        calls.append(articles)
        return "제목", "요약"

    monkeypatch.setattr(entry, "summarize", fake)
    return calls


@pytest.fixture
def two_events_and_noise(engine, article):
    with engine.begin() as conn:
        for _ in range(3):
            article(conn, basis(0))
        for _ in range(3):
            article(conn, basis(1))
        article(conn, basis(2))


def run() -> int:
    with pytest.raises(SystemExit) as exit_info:
        entry.main()
    return exit_info.value.code


def cluster_rows(engine):
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT id, title, summary, updated_at, summarized_at FROM clusters ORDER BY id"
            )
        ).all()


def test_clusters_summarizes_and_logs_the_cost(
    env, engine, two_events_and_noise, summaries, caplog
):
    caplog.set_level(logging.INFO)

    assert run() == 0

    rows = cluster_rows(engine)
    assert len(rows) == 2
    assert all(row.title == "제목" and row.summary == "요약" for row in rows)
    assert len(summaries) == 2
    cost = [r.getMessage() for r in caplog.records if r.getMessage().startswith("clustering cost:")]
    assert len(cost) == 1
    assert "articles=7 clusters=2 noise=1 " in cost[0]
    assert "dbscan_seconds=" in cost[0] and "peak_rss_mib=" in cost[0]


def test_a_second_run_without_new_articles_changes_nothing(
    env, engine, two_events_and_noise, summaries
):
    assert run() == 0
    before = cluster_rows(engine)

    assert run() == 0

    assert cluster_rows(engine) == before
    assert len(summaries) == 2


def test_a_failed_summary_exits_1_and_is_retried(env, engine, two_events_and_noise, monkeypatch):
    def broken(client, articles, **kwargs):
        raise ValueError("bad reply")

    monkeypatch.setattr(entry, "summarize", broken)
    assert run() == 1

    calls = []

    def working(client, articles, **kwargs):
        calls.append(articles)
        return "t", "s"

    monkeypatch.setattr(entry, "summarize", working)
    assert run() == 0
    assert len(calls) == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/news-clusterer/tests/test_main.py -v`
Expected: FAIL (`main()` does not exit, so `pytest.raises(SystemExit)` fails; `entry.summarize` does not exist).

- [ ] **Step 3: Implement `main()`**

`services/news-clusterer/src/news_clusterer/__main__.py`:

```python
import logging
import resource
import sys
import time

import httpx
import sqlalchemy as sa
from ktb_core.logging import setup_logging

from news_clusterer.dbscan import dbscan
from news_clusterer.match import match
from news_clusterer.settings import Settings
from news_clusterer.storage import (
    cluster_articles,
    clusters_needing_summary,
    load_assignment,
    load_embeddings,
    set_summary,
    write_clusters,
)
from news_clusterer.summarize import summarize

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-clusterer started")
    engine = sa.create_engine(settings.postgres_dsn)
    client = httpx.Client()
    failed = 0
    try:
        started = time.perf_counter()
        with engine.connect() as conn:
            article_ids, vectors = load_embeddings(conn)
        loaded = time.perf_counter()
        labels = dbscan(vectors, settings.eps, settings.min_samples)
        clustered = time.perf_counter()

        new: dict[int, set[int]] = {}
        for article_id, label in zip(article_ids, labels.tolist(), strict=True):
            if label != -1:
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

        with engine.connect() as conn:
            pending = clusters_needing_summary(conn)
        for cluster_id in pending:
            try:
                with engine.connect() as conn:
                    articles = cluster_articles(conn, cluster_id)
                title, summary = summarize(
                    client,
                    articles,
                    base_uri=settings.llm_base_uri,
                    model=settings.llm_model,
                    max_chars=settings.summary_max_chars,
                    timeout=settings.llm_timeout,
                )
            except Exception:
                logger.exception("summary failed for cluster %d", cluster_id)
                failed += 1
                continue
            with engine.begin() as conn:
                set_summary(conn, cluster_id, title, summary)
        logger.info("summarized %d clusters, %d failed", len(pending) - failed, failed)
    finally:
        client.close()
        engine.dispose()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the whole suite and lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: all PASS (PostgreSQL tests collected, not skipped); lint clean. If `ruff format --check` fails, run `uv run ruff format .` and re-run.

- [ ] **Step 5: Commit**

```bash
git add services/news-clusterer
git commit -m "feat(clusterer): run DBSCAN, store clusters and summarize them in one cron pass

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Compose job and docs

**Files:**
- Modify: `compose.dev.yaml`, `AGENTS.md`

- [ ] **Step 1: Add the compose job**

In `compose.dev.yaml`, add after the `news-preprocessor` service:

```yaml
  news-clusterer:
    profiles: ["jobs"]
    build:
      context: .
      dockerfile: docker/news-clusterer.Dockerfile
    environment:
      - NEWS_CLUSTERER_POSTGRES_DSN=postgresql+psycopg://ktb:ktb@postgres:5432/news
      - NEWS_CLUSTERER_LLM_BASE_URI
      - NEWS_CLUSTERER_LLM_MODEL
      - NEWS_CLUSTERER_EPS=${NEWS_CLUSTERER_EPS:-0.2}
      - NEWS_CLUSTERER_MIN_SAMPLES=${NEWS_CLUSTERER_MIN_SAMPLES:-3}
      - NEWS_CLUSTERER_LOG_LEVEL=${NEWS_CLUSTERER_LOG_LEVEL:-INFO}
    depends_on:
      postgres:
        condition: service_healthy
    restart: "no"
```

Verify: `docker compose -f compose.dev.yaml --profile jobs config --quiet` exits 0.

- [ ] **Step 2: Update `AGENTS.md`**

In the Commands block, after the `news-preprocessor` compose line, add:

```bash
NEWS_CLUSTERER_LLM_BASE_URI=http://100.bbb.ccc.ddd:8001/v1 NEWS_CLUSTERER_LLM_MODEL=<model> docker compose -f compose.dev.yaml up news-clusterer
```

In the Architecture table, replace the two rows:

```markdown
| `services/news-clusterer` | service | cron: `main()` runs once and exits | `ktb-core` |
| `services/portfolio-builder` | service | work-queue consumer | `ktb-core`, `ktb-market-analyzer` |
```

Replace the first Architecture bullet with:

```markdown
- **Services communicate only through datastores.** news-preprocessor writes articles to PostgreSQL, news-clusterer reads them and writes `clusters` / `article_clusters` back, and portfolio-builder reads both. There are no direct service-to-service calls.
- **news-clusterer recomputes DBSCAN over every embedded article on each run** (design: `docs/superpowers/specs/2026-09-23-news-clusterer-design.md`). Each run logs a `clustering cost:` line with time and peak RSS; that line decides when to move to incremental clustering.
```

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest && uv run ruff check .`
Expected: PASS.

```bash
git add compose.dev.yaml AGENTS.md
git commit -m "docs: document news-clusterer as a cron job; add its compose job

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
