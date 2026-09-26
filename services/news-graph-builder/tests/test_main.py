import logging

import pytest
import sqlalchemy as sa
from news_graph_builder import __main__ as entry
from news_graph_builder.cluster import find_stale_clusters
from news_graph_builder.company import DartCompany
from news_graph_builder.graph import Entity, Extraction, Relation

SAMSUNG = DartCompany("00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930")
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
        "KIWOOM_APP_KEY": "app",
        "KIWOOM_SECRET_KEY": "secret",
        "DART_API_KEY": "dart",
    }.items():
        monkeypatch.setenv(f"NEWS_GRAPH_BUILDER_{name}", value)
    # setup_logging replaces the root handlers, which would detach caplog.
    monkeypatch.setattr(entry, "setup_logging", lambda level: None)


@pytest.fixture
def companies_api(monkeypatch):
    monkeypatch.setattr(entry, "fetch_kospi", lambda client, **kwargs: [("005930", "삼성전자")])
    monkeypatch.setattr(entry, "fetch_corp_codes", lambda api_key: [SAMSUNG])


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


def test_builds_a_graph_for_every_stale_cluster(env, engine, two_clusters, companies_api, llm):
    assert run() == 0

    assert len(llm) == 2
    assert count(engine, "cluster_summaries") == 2
    assert count(engine, "relations") == 2
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT corp_code FROM entities WHERE name = '삼성전자'")
            ).scalar_one()
            == "00126380"
        )


def test_a_second_run_makes_no_llm_call(env, engine, two_clusters, companies_api, llm):
    assert run() == 0

    assert run() == 0

    assert len(llm) == 2


def test_a_failed_extraction_exits_1_and_is_retried(
    env, engine, two_clusters, companies_api, monkeypatch
):
    def broken(client, articles, **kwargs):
        raise ValueError("bad reply")

    monkeypatch.setattr(entry, "extract", broken)
    assert run() == 1
    assert count(engine, "cluster_summaries") == 0

    monkeypatch.setattr(entry, "extract", lambda client, articles, **kwargs: EXTRACTION)
    assert run() == 0
    assert count(engine, "cluster_summaries") == 2


def test_a_failed_first_sync_exits_before_any_llm_call(env, engine, two_clusters, llm, monkeypatch):
    def unreachable(client, **kwargs):
        raise RuntimeError("Kiwoom down")

    monkeypatch.setattr(entry, "fetch_kospi", unreachable)
    monkeypatch.setattr(entry, "fetch_corp_codes", lambda api_key: [SAMSUNG])

    assert run() == 1
    assert llm == []


def test_a_failed_later_sync_still_builds_but_exits_1(
    env, engine, article, cluster, companies_api, llm, monkeypatch
):
    assert run() == 0
    with engine.begin() as conn:
        cluster(conn, [article(conn)])

    def unreachable(api_key):
        raise RuntimeError("DART down")

    monkeypatch.setattr(entry, "fetch_corp_codes", unreachable)

    assert run() == 1
    assert len(llm) == 1
    assert count(engine, "cluster_summaries") == 1


def test_a_cluster_changed_during_extraction_is_skipped(
    env, engine, two_clusters, companies_api, monkeypatch
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


def test_urllib3_debug_logging_is_silenced(env, engine, companies_api, llm):
    assert run() == 0

    assert logging.getLogger("urllib3").level == logging.INFO


def test_a_second_concurrent_run_exits_without_work(env, engine, two_clusters, companies_api, llm):
    with engine.connect() as holder:
        holder.execute(sa.text("SELECT pg_advisory_lock(:id)"), {"id": entry.RUN_LOCK})
        holder.commit()
        try:
            assert run() == 0
            assert llm == []
        finally:
            holder.execute(sa.text("SELECT pg_advisory_unlock(:id)"), {"id": entry.RUN_LOCK})
            holder.commit()
