from datetime import UTC, datetime

import sqlalchemy as sa
from news_graph_builder.cluster import find_cluster_articles, find_stale_clusters, lock_cluster


def summarize(conn, cluster_id: int, cluster_updated_at) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO cluster_summaries (cluster_id, title, summary, cluster_updated_at)"
            " VALUES (:id, 't', 's', :seen)"
        ),
        {"id": cluster_id, "seen": cluster_updated_at},
    )


def test_a_cluster_without_a_summary_is_stale(engine, article, cluster, updated_at):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])

    with engine.connect() as conn:
        clusters = find_stale_clusters(conn)
        seen = updated_at(conn, cluster_id)
        assert clusters == [(cluster_id, seen)]


def test_a_summary_of_the_current_updated_at_is_not_stale(engine, article, cluster, updated_at):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
        summarize(conn, cluster_id, updated_at(conn, cluster_id))

    with engine.connect() as conn:
        clusters = find_stale_clusters(conn)
        assert clusters == []


def test_a_summary_of_an_older_updated_at_is_stale(engine, article, cluster, updated_at):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
        summarize(conn, cluster_id, updated_at(conn, cluster_id))
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE clusters SET updated_at = now()"))

    with engine.connect() as conn:
        clusters = find_stale_clusters(conn)
        assert [row[0] for row in clusters] == [cluster_id]


def test_cluster_articles_are_newest_first(engine, article, cluster):
    with engine.begin() as conn:
        old = article(conn, published_at=datetime(2026, 9, 1, tzinfo=UTC), title="old")
        new = article(conn, published_at=datetime(2026, 9, 2, tzinfo=UTC), title="new")
        cluster_id = cluster(conn, [old, new])

    with engine.connect() as conn:
        articles = find_cluster_articles(conn, cluster_id)
        assert articles == [("new", "body"), ("old", "body")]


def test_lock_fails_when_the_cluster_changed_or_vanished(engine, article, cluster, updated_at):
    with engine.begin() as conn:
        moved = cluster(conn, [article(conn)])
        gone = cluster(conn, [article(conn)])
        seen = {cluster_id: updated_at(conn, cluster_id) for cluster_id in (moved, gone)}
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE clusters SET updated_at = now() WHERE id = :id"), {"id": moved}
        )
        conn.execute(sa.text("DELETE FROM clusters WHERE id = :id"), {"id": gone})

    with engine.begin() as conn:
        assert lock_cluster(conn, moved, seen[moved]) is False
        assert lock_cluster(conn, gone, seen[gone]) is False
