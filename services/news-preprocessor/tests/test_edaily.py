from datetime import datetime, timedelta, timezone

import httpx
from news_preprocessor.sources.publishers import EdailyRSS, publishers

URL = "https://www.edaily.co.kr/News/Read?newsId=05172566645608656"
FEED = f"""<rss><channel>
<item><title>석화업계 부담</title><link>{URL}</link>
<pubDate>Thu, 01 Oct 2026 16:32:50 +0900</pubDate><guid>05172566645608656</guid></item>
<item><title>broken</title><link>{URL}2</link><pubDate>not a date</pubDate></item>
</channel></rss>"""


def test_edaily_feed_and_article(caplog):
    def handler(request):
        if str(request.url) in (
            "http://rss.edaily.co.kr/economy_news.xml",
            "http://rss.edaily.co.kr/stock_news.xml",
        ):
            assert request.headers["accept"] == "text/xml"
            return httpx.Response(200, text=FEED)
        assert str(request.url) == URL
        assert request.headers["accept"] == "text/html"
        return httpx.Response(
            200,
            text='<div class="news_body"><table><tr><td class="caption">사진 설명</td></tr></table>'
            '[이데일리 기자] 실제 기사<br>다음 문장<div class="view_ad02">광고</div></div>',
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    edaily = [
        item for item in publishers(client, "dart-key", {"005930"}) if isinstance(item, EdailyRSS)
    ]
    assert [item.feed_url for item in edaily] == [
        "http://rss.edaily.co.kr/economy_news.xml",
        "http://rss.edaily.co.kr/stock_news.xml",
    ]
    assert {item.source for item in edaily} == {"edaily"}
    source = edaily[0]
    entries = [entry for publisher in edaily for entry in publisher.entries()]
    assert len(entries) == 2
    assert "feed_item_skipped" in caplog.text
    assert entries[0].external_id == URL
    assert entries[0].published_at == datetime(
        2026, 10, 1, 16, 32, 50, tzinfo=timezone(timedelta(hours=9))
    )
    assert "<guid>05172566645608656</guid>" in entries[0].raw_payload
    assert source.article(entries[0]).body == "[이데일리 기자] 실제 기사 다음 문장"


def test_title_only_article_uses_title_as_body():
    def handler(request):
        return httpx.Response(
            200,
            text=FEED
            if str(request.url) == "http://rss.edaily.co.kr/economy_news.xml"
            else '<header><img src="logo.jpg" /></header><div class="news_body"></div>',
        )

    source = EdailyRSS(httpx.Client(transport=httpx.MockTransport(handler)), "economy")
    entry = source.entries()[0]
    assert source.article(entry).body == entry.title
