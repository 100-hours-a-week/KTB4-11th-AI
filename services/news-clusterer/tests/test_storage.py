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
