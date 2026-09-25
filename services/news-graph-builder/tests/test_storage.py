from datetime import UTC, datetime

import sqlalchemy as sa
from news_graph_builder.dart import DartCompany
from news_graph_builder.extract import Entity, Extraction, Relation
from news_graph_builder.resolve import resolve
from news_graph_builder.storage import (
    cluster_articles,
    due_clusters,
    has_companies,
    lock_cluster,
    write_graph,
)
from news_graph_builder.sync_companies import sync_companies


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
        title = conn.execute(sa.text("SELECT title FROM cluster_summaries")).scalar_one()
        assert title == "새 제목"
        assert conn.execute(sa.text("SELECT count(*) FROM cluster_entities")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM relations")).scalar_one() == 0


def test_lock_fails_when_the_cluster_changed_or_vanished(engine, article, cluster):
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


def test_two_names_of_one_company_become_one_cluster_entity(engine, article, cluster):
    with engine.begin() as conn:
        sync_companies(
            conn,
            [("005930", "삼성전자")],
            [DartCompany("00126380", "삼성전자", "Samsung Electronics", "005930")],
        )
        cluster_id = cluster(conn, [article(conn)])
    extraction = Extraction(
        "제목",
        "요약",
        [Entity("삼성전자", "기업"), Entity("Samsung Electronics", "company")],
        [Relation("삼성전자", "Samsung Electronics", "동일", "같은 회사")],
    )

    assert build(engine, cluster_id, extraction) == 0

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM cluster_entities")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM relations")).scalar_one() == 1
