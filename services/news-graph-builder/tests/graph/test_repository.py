import pytest
import sqlalchemy as sa
from news_graph_builder.cluster import lock_cluster, stale_clusters
from news_graph_builder.company import DartCompany, sync_companies
from news_graph_builder.graph import Entity, Extraction, Relation, resolve, write_graph

EXTRACTION = Extraction(
    "제목",
    "요약",
    [Entity("삼성전자", "기업"), Entity("엔비디아", "기업")],
    [
        Relation("삼성전자", "엔비디아", "공급", "HBM 공급"),
        Relation("삼성전자", "애플", "경쟁", "없는 개체"),
    ],
)


@pytest.fixture
def build(engine, updated_at):
    def run(cluster_id: int, extraction=EXTRACTION) -> int:
        with engine.begin() as conn:
            seen = updated_at(conn, cluster_id)
            assert lock_cluster(conn, cluster_id, seen)
            return write_graph(
                conn, cluster_id, seen, extraction, resolve(conn, extraction.entities)
            )

    return run


def test_write_graph_stores_the_summary_and_drops_dangling_relations(
    engine, article, cluster, updated_at, build
):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])

    assert build(cluster_id) == 1

    with engine.connect() as conn:
        summary = conn.execute(sa.text("SELECT * FROM cluster_summaries")).one()
        assert (summary.title, summary.summary) == ("제목", "요약")
        assert summary.cluster_updated_at == updated_at(conn, cluster_id)
        assert conn.execute(sa.text("SELECT count(*) FROM cluster_entities")).scalar_one() == 2
        assert conn.execute(sa.text("SELECT type FROM relations")).scalars().all() == ["공급"]
        assert stale_clusters(conn) == []


def test_rewriting_a_cluster_replaces_its_graph(engine, article, cluster, build):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
    build(cluster_id)

    build(cluster_id, Extraction("새 제목", "새 요약", [Entity("엔비디아", "기업")], []))

    with engine.connect() as conn:
        title = conn.execute(sa.text("SELECT title FROM cluster_summaries")).scalar_one()
        assert title == "새 제목"
        assert conn.execute(sa.text("SELECT count(*) FROM cluster_entities")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM relations")).scalar_one() == 0


def test_a_change_after_the_write_makes_the_cluster_stale_again(engine, article, cluster, build):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
    build(cluster_id)
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE clusters SET updated_at = now()"))

    with engine.connect() as conn:
        assert [row[0] for row in stale_clusters(conn)] == [cluster_id]


def test_deleting_a_cluster_cascades_to_its_graph(engine, article, cluster, build):
    with engine.begin() as conn:
        cluster_id = cluster(conn, [article(conn)])
    build(cluster_id)

    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM clusters"))

    with engine.connect() as conn:
        for table in ("cluster_summaries", "cluster_entities", "relations"):
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert conn.execute(sa.text("SELECT count(*) FROM entities")).scalar_one() == 2


def test_two_names_of_one_company_become_one_cluster_entity(engine, article, cluster, build):
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

    assert build(cluster_id, extraction) == 0

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM cluster_entities")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM relations")).scalar_one() == 1
