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
    monkeypatch.setattr(entry, "publishers", lambda client: ("first", "second"))


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
