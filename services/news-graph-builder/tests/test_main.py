import pytest
import sqlalchemy as sa
from news_graph_builder import __main__ as entry
from news_graph_builder.cluster import find_stale_clusters
from news_graph_builder.graph import Entity, Extraction, Relation

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
    }.items():
        monkeypatch.setenv(f"NEWS_GRAPH_BUILDER_{name}", value)
    # setup_logging replaces the root handlers, which would detach caplog.
    monkeypatch.setattr(entry, "setup_logging", lambda level: None)


@pytest.fixture
def samsung(engine, corporation):
    with engine.begin() as conn:
        corporation(conn, "005930", "삼성전자", "00126380")


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


def test_builds_a_graph_for_every_stale_cluster(env, engine, two_clusters, samsung, llm):
    assert run() == 0

    assert len(llm) == 2
    assert count(engine, "cluster_summaries") == 2
    assert count(engine, "relations") == 2
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT stock_code FROM entities WHERE name = '삼성전자'")
            ).scalar_one()
            == "005930"
        )


def test_a_second_run_makes_no_llm_call(env, engine, two_clusters, samsung, llm):
    assert run() == 0

    assert run() == 0

    assert len(llm) == 2


def test_a_failed_extraction_exits_1_and_is_retried(
    env, engine, two_clusters, samsung, monkeypatch
):
    def broken(client, articles, **kwargs):
        raise ValueError("bad reply")

    monkeypatch.setattr(entry, "extract", broken)
    assert run() == 1
    assert count(engine, "cluster_summaries") == 0

    monkeypatch.setattr(entry, "extract", lambda client, articles, **kwargs: EXTRACTION)
    assert run() == 0
    assert count(engine, "cluster_summaries") == 2


def test_exits_1_before_any_llm_call_when_corporations_is_empty(env, engine, two_clusters, llm):
    assert run() == 1
    assert llm == []
    assert count(engine, "cluster_summaries") == 0


def test_merges_plain_entities_matching_an_alias_before_building(
    env, engine, article, cluster, samsung, monkeypatch
):
    with engine.begin() as conn:
        plain = conn.execute(
            sa.text(
                "INSERT INTO entities (raw_name, name, type)"
                " VALUES ('삼성전자', '삼성전자', '회사') RETURNING id"
            )
        ).scalar_one()
        cluster(conn, [article(conn)])
    seen = []

    def extract(client, articles, **kwargs):
        with engine.connect() as conn:
            seen.append(conn.execute(sa.text("SELECT id, stock_code FROM entities")).all())
        return EXTRACTION

    monkeypatch.setattr(entry, "extract", extract)

    assert run() == 0
    (company,) = seen[0]
    assert company.id != plain
    assert company.stock_code == "005930"


def test_a_cluster_changed_during_extraction_is_skipped(
    env, engine, two_clusters, samsung, monkeypatch
):
    def racing(client, articles, **kwargs):
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE clusters SET updated_at = now()"))
        return EXTRACTION

    monkeypatch.setattr(entry, "extract", racing)

    assert run() == 0
    assert count(engine, "cluster_summaries") == 0
    with engine.connect() as conn:
        clusters = find_stale_clusters(conn)
        assert len(clusters) == 2


def test_a_second_concurrent_run_exits_without_work(env, engine, two_clusters, samsung, llm):
    with engine.connect() as holder:
        holder.execute(sa.text("SELECT pg_advisory_lock(:id)"), {"id": entry.RUN_LOCK})
        holder.commit()
        try:
            assert run() == 0
            assert llm == []
        finally:
            holder.execute(sa.text("SELECT pg_advisory_unlock(:id)"), {"id": entry.RUN_LOCK})
            holder.commit()
