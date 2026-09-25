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
