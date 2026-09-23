import pathlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from news_preprocessor.sources import EmptyBodyError
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
KST = timezone(timedelta(hours=9))


def _source(article: str | None = None) -> HankyungEconomyRSS:
    pages = {
        HankyungEconomyRSS.feed_url: (
            (FIXTURES / "hankyung_feed.xml").read_text(encoding="utf-8"),
            "text/xml",
        )
    }
    if article is not None:
        pages["https://www.hankyung.com/article/202609220001i"] = (article, "text/html")

    def handler(request: httpx.Request) -> httpx.Response:
        body, accept = pages[str(request.url)]
        assert request.headers["accept"] == accept
        return httpx.Response(200, text=body)

    return HankyungEconomyRSS(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_entries_parse_valid_items_and_skip_one_without_pubdate(caplog):
    entries = _source().entries()

    assert [entry.external_id for entry in entries] == [
        "https://www.hankyung.com/article/202609220001i",
        "https://www.hankyung.com/article/202609220002i",
    ]
    assert "skipping hankyung_economy feed item" in caplog.text


def test_entry_fields():
    first = _source().entries()[0]

    assert first.source == "hankyung_economy"
    assert first.url == first.external_id
    assert first.title == '기준금리 동결…"연내 인하 가능성"'
    assert first.published_at == datetime(2026, 9, 22, 15, 1, 6, tzinfo=KST)
    assert first.published_at.utcoffset() == timedelta(hours=9)
    assert first.raw_payload.startswith("<item>")


def test_article_extracts_the_body_only():
    source = _source((FIXTURES / "hankyung_article.html").read_text(encoding="utf-8"))
    entry = source.entries()[0]

    item = source.article(entry)

    assert item.body == (
        "한국은행 전경 한국은행이 기준금리를 연 2.50%로 동결했다. "
        "시장은 예상된 결과라는 반응이다. 전문가들은 연내 인하 가능성을 점쳤다. "
        "김테스트 기자 test@hankyung.com"
    )
    assert item.title == entry.title
    assert item.published_at == entry.published_at


def test_article_without_the_body_container_raises():
    source = _source("<html><body><div class='other'>no body</div></body></html>")
    entry = source.entries()[0]

    with pytest.raises(EmptyBodyError):
        source.article(entry)
