import pathlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from news_preprocessor.sources import EmptyBodyError
from news_preprocessor.sources.publishers.yonhap import YonhapEconomyRSS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
KST = timezone(timedelta(hours=9))


def _source(article: str | None = None) -> YonhapEconomyRSS:
    pages = {
        YonhapEconomyRSS.feed_url: (
            (FIXTURES / "yonhap_feed.xml").read_text(encoding="utf-8"),
            "text/xml",
        )
    }
    if article is not None:
        pages["https://www.yna.co.kr/view/AKR20260924000100001"] = (article, "text/html")

    def handler(request: httpx.Request) -> httpx.Response:
        body, accept = pages[str(request.url)]
        assert request.headers["accept"] == accept
        return httpx.Response(200, text=body)

    return YonhapEconomyRSS(httpx.Client(transport=httpx.MockTransport(handler)))


def test_entries_parse_valid_items_and_skip_one_with_a_broken_pubdate(caplog):
    entries = _source().entries()

    assert [entry.external_id for entry in entries] == [
        "https://www.yna.co.kr/view/AKR20260924000100001",
        "https://www.yna.co.kr/view/AKR20260924000200003",
    ]
    assert "skipping yonhap_economy feed item" in caplog.text


def test_pubdate_is_timezone_aware():
    first = _source().entries()[0]

    assert first.published_at == datetime(2026, 9, 24, 14, 15, 25, tzinfo=KST)


def test_extra_item_children_are_kept_only_in_the_raw_payload():
    first = _source().entries()[0]

    assert first.title == '"세무서 납세자보호위 年1건 미만 개최…제도 개선해야"(종합)'
    assert first.url == first.external_id
    assert "<dc:creator>박테스트</dc:creator>" in first.raw_payload
    assert "요약문은 저장하지 않는다" in first.raw_payload


def test_article_extracts_the_body_only():
    source = _source((FIXTURES / "yonhap_article.html").read_text(encoding="utf-8"))

    item = source.article(source.entries()[0])

    assert item.body == (
        "(서울=연합뉴스) 박테스트 기자 = 납세자보호위원회가 연 1회 미만으로 개최됐다. "
        "세무서 한 곳당 평균 0.6건으로 1년에 한 건도 안 되는 수준이다. test@yna.co.kr"
    )
    assert item.source == "yonhap_economy"


def test_photo_only_article_raises():
    source = _source(
        '<div class="story-news article"><div class="comp-box photo-group">'
        "<figcaption>사진 설명</figcaption></div><p></p>"
        '<p class="txt-copyright adrs">저작권자(c) 연합뉴스</p></div>'
    )

    with pytest.raises(EmptyBodyError):
        source.article(source.entries()[0])
