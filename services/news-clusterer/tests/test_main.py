import logging

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
    monkeypatch.setenv("NEWS_CLUSTERER_LLM_BASE_URI", "http://llm.test/v1")
    monkeypatch.setenv("NEWS_CLUSTERER_LLM_MODEL", "test-model")
    # setup_logging replaces the root handlers, which would detach caplog.
    monkeypatch.setattr(entry, "setup_logging", lambda level: None)


@pytest.fixture
def summaries(monkeypatch):
    calls = []

    def fake(client, articles, **kwargs):
        calls.append(articles)
        return "제목", "요약"

    monkeypatch.setattr(entry, "summarize", fake)
    return calls


@pytest.fixture
def two_events_and_noise(engine, article):
    with engine.begin() as conn:
        for _ in range(3):
            article(conn, basis(0))
        for _ in range(3):
            article(conn, basis(1))
        article(conn, basis(2))


def run() -> int:
    with pytest.raises(SystemExit) as exit_info:
        entry.main()
    return exit_info.value.code


def cluster_rows(engine):
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT id, title, summary, updated_at, summarized_at FROM clusters ORDER BY id"
            )
        ).all()


def test_clusters_summarizes_and_logs_the_cost(
    env, engine, two_events_and_noise, summaries, caplog
):
    caplog.set_level(logging.INFO)

    assert run() == 0

    rows = cluster_rows(engine)
    assert len(rows) == 2
    assert all(row.title == "제목" and row.summary == "요약" for row in rows)
    assert len(summaries) == 2
    cost = [r.getMessage() for r in caplog.records if r.getMessage().startswith("clustering cost:")]
    assert len(cost) == 1
    assert "articles=7 clusters=2 noise=1 " in cost[0]
    assert "dbscan_seconds=" in cost[0] and "peak_rss_mib=" in cost[0]


def test_a_second_run_without_new_articles_changes_nothing(
    env, engine, two_events_and_noise, summaries
):
    assert run() == 0
    before = cluster_rows(engine)

    assert run() == 0

    assert cluster_rows(engine) == before
    assert len(summaries) == 2


def test_a_failed_summary_exits_1_and_is_retried(env, engine, two_events_and_noise, monkeypatch):
    def broken(client, articles, **kwargs):
        raise ValueError("bad reply")

    monkeypatch.setattr(entry, "summarize", broken)
    assert run() == 1

    calls = []

    def working(client, articles, **kwargs):
        calls.append(articles)
        return "t", "s"

    monkeypatch.setattr(entry, "summarize", working)
    assert run() == 0
    assert len(calls) == 2
