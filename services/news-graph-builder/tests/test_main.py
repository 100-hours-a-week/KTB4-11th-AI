import logging

import pytest
import sqlalchemy as sa
from news_graph_builder import __main__ as entry
from news_graph_builder.cluster import find_stale_clusters
from news_graph_builder.company import DartCompany
from news_graph_builder.graph import Entity, Extraction, Relation
from news_graph_builder.theme import Theme, ThemeMember

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
def market_data(monkeypatch):
    tokens = []

    def fetch_token(client, **kwargs):
        tokens.append("tok")
        return "tok"

    monkeypatch.setattr(entry, "fetch_token", fetch_token)
    monkeypatch.setattr(entry, "fetch_kospi", lambda client, **kwargs: [("005930", "삼성전자")])
    monkeypatch.setattr(entry, "fetch_corp_codes", lambda **kwargs: [SAMSUNG])
    monkeypatch.setattr(
        entry, "fetch_themes", lambda client, **kwargs: [Theme("100", "반도체", "삼성전자")]
    )
    monkeypatch.setattr(entry, "fetch_kospi200_codes", lambda client, **kwargs: {"005930"})
    monkeypatch.setattr(
        entry,
        "fetch_theme_members",
        lambda client, **kwargs: {"100": [ThemeMember("005930", "삼성전자")]},
    )
    return tokens


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


def test_builds_a_graph_for_every_stale_cluster(env, engine, two_clusters, market_data, llm):
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


def test_a_second_run_makes_no_llm_call(env, engine, two_clusters, market_data, llm):
    assert run() == 0

    assert run() == 0

    assert len(llm) == 2


def test_a_failed_extraction_exits_1_and_is_retried(
    env, engine, two_clusters, market_data, monkeypatch
):
    def broken(client, articles, **kwargs):
        raise ValueError("bad reply")

    monkeypatch.setattr(entry, "extract", broken)
    assert run() == 1
    assert count(engine, "cluster_summaries") == 0

    monkeypatch.setattr(entry, "extract", lambda client, articles, **kwargs: EXTRACTION)
    assert run() == 0
    assert count(engine, "cluster_summaries") == 2


def test_a_failed_first_sync_exits_before_any_llm_call(
    env, engine, two_clusters, market_data, llm, monkeypatch
):
    def unreachable(client, **kwargs):
        raise RuntimeError("Kiwoom down")

    monkeypatch.setattr(entry, "fetch_kospi", unreachable)

    assert run() == 1
    assert llm == []


def test_a_failed_later_sync_still_builds_but_exits_1(
    env, engine, article, cluster, market_data, llm, monkeypatch
):
    assert run() == 0
    with engine.begin() as conn:
        cluster(conn, [article(conn)])

    def unreachable(**kwargs):
        raise RuntimeError("DART down")

    monkeypatch.setattr(entry, "fetch_corp_codes", unreachable)

    assert run() == 1
    assert len(llm) == 1
    assert count(engine, "cluster_summaries") == 1


def test_a_cluster_changed_during_extraction_is_skipped(
    env, engine, two_clusters, market_data, monkeypatch
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


def test_urllib3_debug_logging_is_silenced(env, engine, market_data, llm):
    assert run() == 0

    assert logging.getLogger("urllib3").level == logging.INFO


def test_a_second_concurrent_run_exits_without_work(env, engine, two_clusters, market_data, llm):
    with engine.connect() as holder:
        holder.execute(sa.text("SELECT pg_advisory_lock(:id)"), {"id": entry.RUN_LOCK})
        holder.commit()
        try:
            assert run() == 0
            assert llm == []
        finally:
            holder.execute(sa.text("SELECT pg_advisory_unlock(:id)"), {"id": entry.RUN_LOCK})
            holder.commit()


def theme_rows(engine):
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT theme_code, corp_code, is_main FROM theme_companies")
        ).all()


def test_syncs_themes_with_one_token(env, engine, two_clusters, market_data, llm):
    assert run() == 0

    assert [tuple(row) for row in theme_rows(engine)] == [("100", "00126380", True)]
    assert market_data == ["tok"]


def test_a_failed_theme_sync_keeps_old_themes_and_still_builds(
    env, engine, article, cluster, market_data, llm, monkeypatch
):
    assert run() == 0
    with engine.begin() as conn:
        cluster(conn, [article(conn)])

    def unreachable(client, **kwargs):
        raise RuntimeError("Kiwoom theme API down")

    monkeypatch.setattr(entry, "fetch_themes", unreachable)

    assert run() == 1
    assert len(llm) == 1
    assert [tuple(row) for row in theme_rows(engine)] == [("100", "00126380", True)]


def test_a_failed_token_fails_both_syncs(env, engine, two_clusters, market_data, llm, monkeypatch):
    def refused(client, **kwargs):
        raise RuntimeError("Kiwoom token refused")

    monkeypatch.setattr(entry, "fetch_token", refused)

    assert run() == 1
    assert llm == []
    assert theme_rows(engine) == []
