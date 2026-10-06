from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

import httpx
import sqlalchemy as sa
from news_preprocessor.scrape import scrape
from news_preprocessor.sources import EmptyBodyError, FeedEntry
from news_preprocessor.sources.publishers import HankyungEconomyRSS, MaeilBusinessEconomyRSS
from news_preprocessor.storage import articles

HANKYUNG_ARTICLES = [
    "https://www.hankyung.com/article/202609220001i",
    "https://www.hankyung.com/article/202609220002i",
]


def _sources(engine):
    with engine.connect() as conn:
        query = sa.select(articles.c.source).order_by(articles.c.id)
        return list(conn.execute(query).scalars())


def test_stores_new_articles_and_reports_their_ids(engine, fake_web):
    hankyung, _ = fake_web().sources()

    result = scrape(engine, hankyung)

    assert result.succeed == HANKYUNG_ARTICLES
    assert result.failed == []
    assert _sources(engine) == ["hankyung_economy"] * 2


def test_a_second_scrape_reports_nothing_and_fetches_only_the_feed(engine, fake_web):
    scrape(engine, fake_web().sources()[0])
    web = fake_web()

    result = scrape(engine, web.sources()[0])

    assert result.succeed == []
    assert result.failed == []
    assert len(_sources(engine)) == 2
    assert web.requested == [HankyungEconomyRSS.feed_url]


def test_a_failing_feed_reports_the_feed_url_and_stores_nothing(engine, fake_web):
    web = fake_web(failing_feeds=(MaeilBusinessEconomyRSS.feed_url,))

    result = scrape(engine, web.sources()[1])

    assert result.succeed == []
    assert result.failed == [MaeilBusinessEconomyRSS.feed_url]
    assert _sources(engine) == []


def test_a_failing_article_is_reported_and_the_others_are_stored(engine, fake_web, monkeypatch):
    hankyung, _ = fake_web().sources()
    article = hankyung.article

    def fail_the_first(entry):
        if entry.external_id == HANKYUNG_ARTICLES[0]:
            raise RuntimeError("page unreachable")
        return article(entry)

    monkeypatch.setattr(hankyung, "article", fail_the_first)

    result = scrape(engine, hankyung)

    assert result.succeed == [HANKYUNG_ARTICLES[1]]
    assert result.failed == [HANKYUNG_ARTICLES[0]]
    assert len(_sources(engine)) == 1


def test_an_empty_body_article_is_skipped_with_a_warning(engine, fake_web, monkeypatch):
    hankyung, _ = fake_web().sources()
    article = hankyung.article

    def return_an_empty_body(entry):
        if entry.external_id == HANKYUNG_ARTICLES[0]:
            raise EmptyBodyError(entry.url)
        return article(entry)

    monkeypatch.setattr(hankyung, "article", return_an_empty_body)

    with patch("news_preprocessor.scrape.log.warning") as warning:
        result = scrape(engine, hankyung)

    assert result.succeed == [HANKYUNG_ARTICLES[1]]
    assert result.failed == []
    warning.assert_any_call(
        "empty_body_article",
        source="hankyung_economy",
        url=HANKYUNG_ARTICLES[0],
        article_id=HANKYUNG_ARTICLES[0],
    )


def _source_with_http_error(status_code: int) -> tuple[Mock, FeedEntry, FeedEntry]:
    entry = FeedEntry(
        source="test",
        external_id="article-1",
        url="https://example.com/article-1",
        title="title",
        published_at=datetime(2026, 10, 6, tzinfo=UTC),
        raw_payload="payload",
    )
    next_entry = FeedEntry(
        source="test",
        external_id="article-2",
        url="https://example.com/article-2",
        title="next title",
        published_at=datetime(2026, 10, 6, tzinfo=UTC),
        raw_payload="next payload",
    )
    request = httpx.Request("GET", entry.url)
    response = httpx.Response(status_code, request=request)
    source = Mock(source="test", feed_url="https://example.com/feed")
    source.entries.return_value = [entry, next_entry]
    source.article.side_effect = [
        httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response),
        next_entry,
    ]
    return source, entry, next_entry


def test_a_not_found_article_is_skipped_without_failing_the_scrape():
    source, entry, next_entry = _source_with_http_error(404)
    engine = MagicMock()

    with (
        patch("news_preprocessor.scrape.known_external_ids", return_value=set()),
        patch("news_preprocessor.scrape.insert_new", return_value=True),
        patch("news_preprocessor.scrape.log.warning") as warning,
    ):
        result = scrape(engine, source)

    assert result.succeed == [next_entry.external_id]
    assert result.failed == []
    warning.assert_any_call(
        "unavailable_article",
        source="test",
        url=entry.url,
        article_id=entry.external_id,
        status_code=404,
        final_url=entry.url,
    )


def test_a_server_error_article_remains_a_scrape_failure():
    source, entry, next_entry = _source_with_http_error(503)
    engine = MagicMock()

    with patch("news_preprocessor.scrape.known_external_ids", return_value=set()):
        result = scrape(engine, source)

    assert result.succeed == [next_entry.external_id]
    assert result.failed == [entry.external_id]
