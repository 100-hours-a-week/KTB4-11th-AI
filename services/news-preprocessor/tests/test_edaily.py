from datetime import datetime, timedelta, timezone

import httpx
import pytest
from news_preprocessor.sources import EmptyBodyError
from news_preprocessor.sources.publishers import EdailyRSS, publishers

URL = "https://www.edaily.co.kr/News/Read?newsId=05172566645608656"
FEED = f"""<rss><channel>
<item><title>석화업계 부담</title><link>{URL}</link>
<pubDate>Thu, 01 Oct 2026 16:32:50 +0900</pubDate><guid>05172566645608656</guid></item>
<item><title>broken</title><link>{URL}2</link><pubDate>not a date</pubDate></item>
</channel></rss>"""


def test_edaily_feed_and_article(caplog):
    def handler(request):
        if str(request.url) == EdailyRSS.feed_url:
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
    source = EdailyRSS(client)
    assert any(isinstance(item, EdailyRSS) for item in publishers(client))
    entries = source.entries()
    assert len(entries) == 1
    assert "feed_item_skipped" in caplog.text
    assert entries[0].external_id == URL
    assert entries[0].published_at == datetime(
        2026, 10, 1, 16, 32, 50, tzinfo=timezone(timedelta(hours=9))
    )
    assert "<guid>05172566645608656</guid>" in entries[0].raw_payload
    assert source.article(entries[0]).body == "[이데일리 기자] 실제 기사 다음 문장"


def test_empty_article_raises():
    def handler(request):
        return httpx.Response(
            200,
            text=FEED
            if str(request.url) == EdailyRSS.feed_url
            else '<div class="news_body"><table><tr><td>사진</td></tr></table></div>',
        )

    source = EdailyRSS(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(EmptyBodyError):
        source.article(source.entries()[0])
