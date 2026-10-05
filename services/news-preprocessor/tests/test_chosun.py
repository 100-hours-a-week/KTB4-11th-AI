from datetime import datetime, timezone

import httpx
from news_preprocessor.sources.publishers import ChosunEconomyRSS, publishers

FEED = """<rss><channel>
<item><title>경제 기사</title><link>https://www.chosun.com/economy/tech_it/2026/10/01/ABC/</link>
<pubDate>Thu, 01 Oct 2026 06:20:56 +0000</pubDate></item>
<item><title>스포츠 기사</title><link>https://www.chosun.com/sports/golf/2026/10/01/DEF/</link>
<pubDate>Thu, 01 Oct 2026 06:20:56 +0000</pubDate></item>
</channel></rss>"""
ARTICLE = (
    '<script>Fusion.globalContent={"content_elements":[{"type":"image"},'
    '{"type":"text","content":"첫 문단 <b>강조</b>"},'
    '{"type":"text","content":"둘째 문단"}]};</script>'
)


def test_chosun_economy_feed_and_article():
    def handler(request):
        text = FEED if str(request.url) == ChosunEconomyRSS.feed_url else ARTICLE
        return httpx.Response(200, text=text)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = ChosunEconomyRSS(client)
    assert any(isinstance(publisher, ChosunEconomyRSS) for publisher in publishers(client))
    entries = source.entries()
    assert len(entries) == 1
    assert entries[0].source == "chosun_economy"
    assert entries[0].published_at == datetime(2026, 10, 1, 6, 20, 56, tzinfo=timezone.utc)
    assert source.article(entries[0]).body == "첫 문단 강조 둘째 문단"


def test_title_only_article_without_content_marker_uses_title_as_body():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text="")))
    source = ChosunEconomyRSS(client)
    entry = ChosunEconomyRSS(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=FEED)))
    ).entries()[0]
    assert source.article(entry).body == entry.title
