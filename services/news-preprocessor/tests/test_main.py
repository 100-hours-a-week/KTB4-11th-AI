import logging
from unittest.mock import MagicMock

import pytest
from news_preprocessor import __main__ as entry
from news_preprocessor.embed_pending import EmbedResult
from news_preprocessor.scrape import ScrapeResult

FEED = "https://www.mk.co.kr/rss/30100041/"
OK = ScrapeResult(succeed=[], failed=[])
BROKEN = ScrapeResult(succeed=[], failed=[FEED])


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


@pytest.mark.parametrize(
    ("scrape_results", "embed_result", "code"),
    [
        ((OK, OK), EmbedResult(succeed=[], failed=[]), 0),
        ((OK, BROKEN), EmbedResult(succeed=[], failed=[]), 1),
        ((OK, OK), EmbedResult(succeed=[], failed=[FEED]), 1),
    ],
)
def test_exit_code_reflects_every_step(env, monkeypatch, scrape_results, embed_result, code):
    results = iter(scrape_results)
    monkeypatch.setattr(entry, "scrape", lambda engine, source: next(results))
    monkeypatch.setattr(entry, "embed_pending", lambda engine, embedder, limit: embed_result)

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == code


def test_a_failing_source_does_not_stop_the_others(env, monkeypatch):
    scraped = []
    monkeypatch.setattr(entry, "scrape", lambda engine, source: scraped.append(source) or BROKEN)
    monkeypatch.setattr(
        entry, "embed_pending", lambda engine, embedder, limit: EmbedResult(succeed=[], failed=[])
    )

    with pytest.raises(SystemExit):
        entry.main()

    assert scraped == ["first", "second"]


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

    with pytest.raises(SystemExit):
        entry.main()

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

    with pytest.raises(SystemExit):
        entry.main()

    out = capsys.readouterr().out
    assert "dart-key" not in out
    assert "crtfc_key=***&page_no=1" in out
