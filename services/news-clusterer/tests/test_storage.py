from datetime import datetime

import numpy as np
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
from news_clusterer.match import match
from news_clusterer.storage import load_assignment, load_embeddings, write_clusters


def basis(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[index] = 1.0
    return vector


def apply(engine, new):
    with engine.begin() as conn:
        old = load_assignment(conn)
        matches, unmatched = match(new, old)
        write_clusters(conn, new, old, matches, unmatched)


def updated_at(engine) -> dict[int, datetime]:
    with engine.connect() as conn:
        return dict(conn.exec_driver_sql("SELECT id, updated_at FROM clusters").all())


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
