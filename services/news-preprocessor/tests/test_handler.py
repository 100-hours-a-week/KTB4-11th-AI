import pytest
from news_preprocessor import handler as entry
from news_preprocessor.embed_pending import EmbedResult
from news_preprocessor.scrape import ScrapeResult

ARTICLE = "https://www.hankyung.com/article/202609220001i"
FEED = "https://www.mk.co.kr/rss/30100041/"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "postgresql+psycopg://u@unused.invalid/db")
    monkeypatch.setattr(entry, "publishers", lambda client: ("first", "second"))


def _arrange(monkeypatch, scrape_results, embed_result):
    results = iter(scrape_results)
    monkeypatch.setattr(entry, "scrape", lambda engine, source: next(results))
    monkeypatch.setattr(entry, "embed_pending", lambda engine, embedder, limit: embed_result)


def test_reports_what_each_step_did(env, monkeypatch):
    _arrange(
        monkeypatch,
        [ScrapeResult(succeed=[ARTICLE], failed=[]), ScrapeResult(succeed=[], failed=[])],
        EmbedResult(succeed=[ARTICLE], failed=[]),
    )

    assert entry.handler({}, None) == {
        "scraped": {"succeed": [ARTICLE], "succeed_count": 1, "failed": [], "failed_count": 0},
        "embedded": {"succeed": [ARTICLE], "succeed_count": 1, "failed": [], "failed_count": 0},
    }


def test_a_failed_source_raises_after_every_source_ran(env, monkeypatch):
    scraped = []
    monkeypatch.setattr(
        entry,
        "scrape",
        lambda engine, source: scraped.append(source) or ScrapeResult(succeed=[], failed=[FEED]),
    )
    monkeypatch.setattr(
        entry, "embed_pending", lambda engine, embedder, limit: EmbedResult(succeed=[], failed=[])
    )

    with pytest.raises(RuntimeError, match=FEED):
        entry.handler({}, None)

    assert scraped == ["first", "second"]


def test_a_failed_embedding_raises(env, monkeypatch):
    _arrange(
        monkeypatch,
        [ScrapeResult(succeed=[], failed=[]), ScrapeResult(succeed=[], failed=[])],
        EmbedResult(succeed=[], failed=[ARTICLE]),
    )

    with pytest.raises(RuntimeError, match=ARTICLE):
        entry.handler({}, None)
