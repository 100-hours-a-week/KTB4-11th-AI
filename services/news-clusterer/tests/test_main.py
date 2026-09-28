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
