import logging
from contextlib import nullcontext
from types import SimpleNamespace

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
    monkeypatch.setattr(entry, "setup_logging", lambda level, service_name: None)


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
    cost = [r for r in caplog.records if r.getMessage() == "clustering cost:"]
    assert len(cost) == 1
    assert {key: cost[0].fields[key] for key in ("articles", "clusters", "noise")} == {
        "articles": 7,
        "clusters": 2,
        "noise": 1,
    }
    assert "dbscan_seconds" in cost[0].fields and "peak_rss_mib" in cost[0].fields
    embedding_loaded = next(
        record for record in caplog.records if record.getMessage() == "embedding_loaded"
    )
    assert embedding_loaded.fields["article_count"] == 7
    assert "load_seconds" in embedding_loaded.fields
    clustering_completed = next(
        record for record in caplog.records if record.getMessage() == "clustering_completed"
    )
    assert {
        key: clustering_completed.fields[key]
        for key in ("article_count", "cluster_count", "noise_count")
    } == {"article_count": 7, "cluster_count": 2, "noise_count": 1}
    assert "clustering_seconds" in clustering_completed.fields
    assignment_loaded = next(
        record for record in caplog.records if record.getMessage() == "assignment_loaded"
    )
    assert assignment_loaded.fields["existing_cluster_count"] == 0
    assert "assignment_seconds" in assignment_loaded.fields
    cluster_matched = next(
        record for record in caplog.records if record.getMessage() == "clusters_matched"
    )
    assert cluster_matched.fields["matched_cluster_count"] == 0
    assert cluster_matched.fields["old_cluster_count_to_delete"] == 0
    assert "matching_seconds" in cluster_matched.fields
    started = [record for record in caplog.records if record.getMessage() == "run_start"]
    assert len(started) == 1
    assert started[0].fields == {"eps": 0.36, "min_samples": 2, "embedding_dimensions": 2000}
    result = [record for record in caplog.records if record.getMessage() == "cluster_write_result"]
    assert len(result) == 1
    assert {
        key: result[0].fields[key]
        for key in (
            "clusters_created",
            "clusters_maintained",
            "clusters_changed",
            "clusters_deleted",
            "mappings_added",
            "mappings_moved",
            "mappings_removed",
        )
    } == {
        "clusters_created": 2,
        "clusters_maintained": 0,
        "clusters_changed": 0,
        "clusters_deleted": 0,
        "mappings_added": 6,
        "mappings_moved": 0,
        "mappings_removed": 0,
    }
    assert {"assignment_seconds", "matching_seconds", "write_seconds", "total_seconds"} <= result[
        0
    ].fields.keys()


def test_a_second_run_without_new_articles_changes_nothing(
    env, engine, two_events_and_noise, caplog
):
    entry.main()
    before = cluster_rows(engine)
    caplog.clear()

    entry.main()

    assert cluster_rows(engine) == before
    result = next(
        record for record in caplog.records if record.getMessage() == "cluster_write_result"
    )
    assert result.fields == {
        "clusters_created": 0,
        "clusters_maintained": 2,
        "clusters_changed": 0,
        "clusters_deleted": 0,
        "mappings_added": 0,
        "mappings_moved": 0,
        "mappings_removed": 0,
        "assignment_seconds": result.fields["assignment_seconds"],
        "matching_seconds": result.fields["matching_seconds"],
        "write_seconds": result.fields["write_seconds"],
    }


def test_logs_the_embedding_load_stage_and_traceback_on_failure(monkeypatch, caplog):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", "postgresql+psycopg://test")
    monkeypatch.setattr(entry, "setup_logging", lambda level, service_name: None)
    caplog.set_level(logging.INFO)
    engine = SimpleNamespace(connect=lambda: nullcontext(None), dispose=lambda: None)
    monkeypatch.setattr(entry.sa, "create_engine", lambda _: engine)

    def fail(_):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(entry, "load_embeddings", fail)

    with pytest.raises(RuntimeError, match="database unavailable"):
        entry.main()

    failed = next(
        record for record in caplog.records if record.getMessage() == "clusterer_run_failed"
    )
    assert failed.fields["stage"] == "embedding_load"
    assert failed.fields["error_type"] == "RuntimeError"
    assert failed.fields["error_message"] == "database unavailable"
    assert failed.exc_info is not None


def test_logs_the_clustering_stage_and_traceback_on_failure(monkeypatch, caplog):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", "postgresql+psycopg://test")
    monkeypatch.setattr(entry, "setup_logging", lambda level, service_name: None)
    caplog.set_level(logging.INFO)
    engine = SimpleNamespace(connect=lambda: nullcontext(None), dispose=lambda: None)
    monkeypatch.setattr(entry.sa, "create_engine", lambda _: engine)
    monkeypatch.setattr(entry, "load_embeddings", lambda _: ([1], []))

    def fail(*_):
        raise RuntimeError("clustering failed")

    monkeypatch.setattr(entry, "dbscan", fail)

    with pytest.raises(RuntimeError, match="clustering failed"):
        entry.main()

    failed = next(
        record for record in caplog.records if record.getMessage() == "clusterer_run_failed"
    )
    assert failed.fields["stage"] == "clustering"
    assert failed.fields["error_type"] == "RuntimeError"
    assert failed.fields["error_message"] == "clustering failed"
    assert failed.exc_info is not None


def test_logs_the_cluster_write_stage_and_traceback_on_failure(monkeypatch, caplog):
    monkeypatch.setenv("NEWS_CLUSTERER_POSTGRES_DSN", "postgresql+psycopg://test")
    monkeypatch.setattr(entry, "setup_logging", lambda level, service_name: None)
    caplog.set_level(logging.INFO)
    engine = SimpleNamespace(
        connect=lambda: nullcontext(None),
        begin=lambda: nullcontext(None),
        dispose=lambda: None,
    )
    monkeypatch.setattr(entry.sa, "create_engine", lambda _: engine)
    monkeypatch.setattr(entry, "load_embeddings", lambda _: ([1], []))
    monkeypatch.setattr(entry, "dbscan", lambda *_: SimpleNamespace(tolist=lambda: [0]))
    monkeypatch.setattr(entry, "load_assignment", lambda _: {})
    monkeypatch.setattr(entry, "match", lambda *_: ({0: None}, set()))

    def fail(*_):
        raise RuntimeError("write failed")

    monkeypatch.setattr(entry, "write_clusters", fail)

    with pytest.raises(RuntimeError, match="write failed"):
        entry.main()

    failed = next(
        record for record in caplog.records if record.getMessage() == "clusterer_run_failed"
    )
    assert failed.fields["stage"] == "cluster_write"
    assert failed.fields["error_type"] == "RuntimeError"
    assert failed.fields["error_message"] == "write failed"
    assert failed.exc_info is not None
