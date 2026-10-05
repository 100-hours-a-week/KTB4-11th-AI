import logging
from unittest.mock import MagicMock

import pytest
from news_preprocessor import handler as entry
from news_preprocessor.embed_pending import EmbedResult
from news_preprocessor.scrape import ScrapeResult

ARTICLE = "https://www.hankyung.com/article/202609220001i"
FEED = "https://www.mk.co.kr/rss/30100041/"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "postgresql+psycopg://u@unused.invalid/db")
    monkeypatch.setenv("NEWS_PREPROCESSOR_DART_API_KEY", "dart-key")
    # The entry point reads corporation_indices before scraping; no database here.
    monkeypatch.setattr(entry.sa, "create_engine", lambda dsn: MagicMock())
    monkeypatch.setattr(entry, "kospi200_stock_codes", lambda conn: {"005930"})
    monkeypatch.setattr(
        entry, "publishers", lambda client, dart_api_key, stock_codes: ("first", "second")
    )


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


def test_publishers_get_the_dart_key_and_kospi200_codes(env, monkeypatch):
    received = []
    monkeypatch.setattr(
        entry,
        "publishers",
        lambda client, dart_api_key, stock_codes: (
            received.append((dart_api_key, stock_codes)) or ()
        ),
    )
    monkeypatch.setattr(
        entry, "embed_pending", lambda engine, embedder, limit: EmbedResult(succeed=[], failed=[])
    )

    entry.handler({}, None)

    assert received == [("dart-key", {"005930"})]


def test_dart_keys_in_urls_are_masked_in_logs(env, monkeypatch, capsys):
    def publishers(client, dart_api_key, stock_codes):
        logging.getLogger("httpx").info(
            "HTTP Request: GET https://opendart.fss.or.kr/api/list.json?crtfc_key=%s&page_no=1",
            dart_api_key,
        )
        return ()

    monkeypatch.setattr(entry, "publishers", publishers)
    monkeypatch.setattr(
        entry, "embed_pending", lambda engine, embedder, limit: EmbedResult(succeed=[], failed=[])
    )

    entry.handler({}, None)

    out = capsys.readouterr().out
    assert "dart-key" not in out
    assert "crtfc_key=***&page_no=1" in out
