import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from news_preprocessor.sources import EmptyBodyError
from news_preprocessor.sources.publishers.maeil import MaeilBusinessEconomyRSS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
KST = timezone(timedelta(hours=9))


def _fake_fetch(pages: dict[str, str]):
    def fetch(url: str, content_type: str) -> str:
        assert content_type == (
            "application/xml" if url == MaeilBusinessEconomyRSS.feed_url else "text/html"
        )
        return pages[url]

    return fetch


def _source(article: str | None = None) -> MaeilBusinessEconomyRSS:
    pages = {
        MaeilBusinessEconomyRSS.feed_url: (FIXTURES / "maeil_feed.xml").read_text(encoding="utf-8")
    }
    if article is not None:
        pages["https://www.mk.co.kr/news/economy/10000001"] = article
    return MaeilBusinessEconomyRSS(fetch=_fake_fetch(pages))


def test_entries_parse_valid_items_and_skip_one_with_a_broken_pubdate(caplog):
    entries = _source().entries()

    assert [entry.external_id for entry in entries] == [
        "https://www.mk.co.kr/news/economy/10000001",
        "https://www.mk.co.kr/news/economy/10000002",
    ]
    assert "skipping maeil_business_economy feed item" in caplog.text


def test_colon_offset_pubdate_is_timezone_aware():
    first = _source().entries()[0]

    assert first.published_at.tzinfo is not None
    assert first.published_at.utcoffset() == timedelta(hours=9)
    assert first.published_at == datetime(2026, 9, 22, 14, 37, 38, tzinfo=KST)


def test_extra_item_children_are_kept_only_in_the_raw_payload():
    first = _source().entries()[0]

    assert first.title == "“집값 잡으려 어쩔수 없지만”…가계이자 부담 늘어"
    assert first.url == first.external_id
    assert "<no>10000001</no>" in first.raw_payload
    assert "요약문은 저장하지 않는다" in first.raw_payload


def test_article_extracts_the_body_only():
    source = _source((FIXTURES / "maeil_article.html").read_text(encoding="utf-8"))

    item = source.article(source.entries()[0])

    assert item.body == (
        "한국은행이 기준금리를 0.25%포인트 올리면 가계 이자 부담이 3조원 늘어난다. "
        "연체율은 15개월 뒤 정점에 이를 전망이다.[최테스트 기자]"
    )
    assert item.source == "maeil_business_economy"


def test_article_without_the_body_container_raises():
    source = _source("<html><body><p>no body</p></body></html>")

    with pytest.raises(EmptyBodyError):
        source.article(source.entries()[0])
