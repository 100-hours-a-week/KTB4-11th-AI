from datetime import datetime, timedelta, timezone

import httpx
import pytest
from news_preprocessor.sources import EmptyBodyError
from news_preprocessor.sources.publishers import SeoulEconomicRSS, publishers

FEED_URL = "https://m.sedaily.com/rss/business"
URL = "https://m.sedaily.com/article/20097528"
FEED = f"""<rss version="2.0"><channel>
<item><title><![CDATA[신한은행 해킹 추정 서버]]></title><link><![CDATA[{URL}]]></link>
<author><![CDATA[노현섭]]></author><pubDate>Fri, 02 Oct 2026 11:53:40 +0900</pubDate></item>
<item><title>broken</title><link>{URL}1</link><pubDate>not a date</pubDate></item>
</channel></rss>"""
ARTICLE = """<div class="view" id="article-body" itemprop="articleBody">
<div class="article-photo-wrap"><figure><img src="x.jpeg"/><figcaption>사진 설명</figcaption>
</figure></div><p class="align-l">실제 기사</p><div class="article-video"><iframe></iframe>
영상</div><p class="align-l"><span stockcode="263860">지니언스(263860)</span> 다음 문장</p></div>"""


def test_sedaily_feed_and_article(caplog):
    def handler(request):
        if str(request.url) == FEED_URL:
            assert request.headers["accept"] == "text/xml"
            return httpx.Response(200, text=FEED)
        assert str(request.url) == URL
        assert request.headers["accept"] == "text/html"
        return httpx.Response(200, text=ARTICLE)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = SeoulEconomicRSS(client, "business")
    sedaily = [item for item in publishers(client) if isinstance(item, SeoulEconomicRSS)]
    assert [item.feed_url for item in sedaily] == [
        f"https://m.sedaily.com/rss/{section}"
        for section in ("business", "market", "economy", "finance", "international")
    ]
    assert {item.source for item in sedaily} == {"sedaily"}
    entries = source.entries()
    assert len(entries) == 1
    assert "feed_item_skipped" in caplog.text
    assert entries[0].source == "sedaily"
    assert entries[0].external_id == URL
    assert entries[0].title == "신한은행 해킹 추정 서버"
    assert entries[0].published_at == datetime(
        2026, 10, 2, 11, 53, 40, tzinfo=timezone(timedelta(hours=9))
    )
    assert "노현섭" in entries[0].raw_payload
    assert source.article(entries[0]).body == "사진 설명 실제 기사 지니언스(263860) 다음 문장"


def test_empty_article_raises():
    def handler(request):
        return httpx.Response(
            200,
            text=FEED
            if str(request.url) == FEED_URL
            else '<div id="article-body"><div class="article-video"><iframe></iframe></div></div>',
        )

    source = SeoulEconomicRSS(httpx.Client(transport=httpx.MockTransport(handler)), "business")
    with pytest.raises(EmptyBodyError):
        source.article(source.entries()[0])
