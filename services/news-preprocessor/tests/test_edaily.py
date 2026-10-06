from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import sqlalchemy as sa
from news_preprocessor.scrape import scrape
from news_preprocessor.sources import FeedEntry
from news_preprocessor.sources.publishers import EdailyRSS, publishers
from news_preprocessor.storage import articles

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
    edaily = [item for item in publishers(client) if isinstance(item, EdailyRSS)]
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


MISSING_ID = "33902086645610296"
MISSING_URL = f"http://www.edaily.co.kr/news/newspath.asp?newsid={MISSING_ID}"
FINAL_URL = f"https://www.edaily.co.kr/News/Read?newsId={MISSING_ID}&mediaCodeNo=257"
TV_URL = f"https://tvm.edaily.co.kr/News/NewsRead?Kind=&NewsId={MISSING_ID}"


@pytest.fixture
def missing_entry():
    return FeedEntry(
        source="edaily",
        external_id=MISSING_URL,
        url=MISSING_URL,
        title="시장은 기다려주지 않는다",
        published_at=datetime(2026, 10, 6, 14, 15, tzinfo=timezone(timedelta(hours=9))),
        raw_payload="original RSS item",
    )


def test_edaily_404_recovers_same_article_from_tv_with_working_url(missing_entry):
    def handler(request):
        if str(request.url) == MISSING_URL:
            return httpx.Response(302, headers={"Location": FINAL_URL})
        if str(request.url) == FINAL_URL:
            return httpx.Response(404, text='<script>window.location.replace("/")</script>')
        assert str(request.url) == TV_URL
        return httpx.Response(
            200,
            text='<div class="news_text"><p>동일 기사 실제 본문</p>'
            "<script>광고</script><iframe>광고</iframe></div><aside>관련 기사</aside>",
        )

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        item = EdailyRSS(client, "stock").article(missing_entry)

    assert item.body == "동일 기사 실제 본문"
    assert item.url == TV_URL
    assert item.external_id == MISSING_URL
    assert item.raw_payload == missing_entry.raw_payload


@pytest.mark.parametrize("status", [403, 410, 429, 500, 503])
def test_edaily_non_404_errors_are_not_redirected_to_tv(missing_entry, status):
    def handler(request):
        assert str(request.url) == MISSING_URL
        return httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as failure:
            EdailyRSS(client, "stock").article(missing_entry)
    assert failure.value.response.status_code == status


def test_edaily_tv_404_remains_a_failure(missing_entry):
    def handler(request):
        assert str(request.url) in (MISSING_URL, TV_URL)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as failure:
            EdailyRSS(client, "stock").article(missing_entry)
    assert str(failure.value.response.url) == TV_URL


@pytest.mark.parametrize(
    "html",
    [
        "<main>이데일리 TV 메인 페이지</main>",
        '<div class="news_text"> </div>',
        '<div class="news_text"><script>광고</script><iframe>광고</iframe></div>',
        '<div class="news_text"><img src="article.jpg" /></div>',
    ],
)
def test_edaily_tv_missing_body_is_not_saved_as_title_only_news(missing_entry, html):
    def handler(request):
        if str(request.url) == MISSING_URL:
            return httpx.Response(404)
        assert str(request.url) == TV_URL
        return httpx.Response(200, text=html)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="article body"):
            EdailyRSS(client, "stock").article(missing_entry)


def test_edaily_direct_read_url_recovers_from_tv(missing_entry):
    def handler(request):
        if str(request.url) == FINAL_URL:
            return httpx.Response(404)
        assert str(request.url) == TV_URL
        return httpx.Response(200, text='<div class="news_text">원문 본문</div>')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        item = EdailyRSS(client, "stock").article(replace(missing_entry, url=FINAL_URL))
    assert item.url == TV_URL
    assert item.body == "원문 본문"


@pytest.mark.parametrize(
    ("url", "tv_url"),
    [
        (
            "https://www.edaily.co.kr/news/newspath.asp?newsid=invalid",
            "https://tvm.edaily.co.kr/News/NewsRead?Kind=&NewsId=invalid",
        ),
        (f"https://www.edaily.co.kr/other?newsid={MISSING_ID}", TV_URL),
        (f"https://example.com/News/Read?newsId={MISSING_ID}", TV_URL),
    ],
)
def test_edaily_404_with_news_id_attempts_tv_recovery(missing_entry, url, tv_url):
    def handler(request):
        assert str(request.url) in (url, tv_url)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as failure:
            EdailyRSS(client, "stock").article(replace(missing_entry, url=url))

    assert str(failure.value.response.url) == tv_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.edaily.co.kr/news/newspath.asp",
        "https://www.edaily.co.kr/news/newspath.asp?newsid=",
    ],
)
def test_edaily_missing_news_id_does_not_guess_a_tv_url(missing_entry, url):
    def handler(request):
        assert str(request.url) == url
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            EdailyRSS(client, "stock").article(replace(missing_entry, url=url))


def test_edaily_tv_timeout_remains_a_failure(missing_entry):
    def handler(request):
        if str(request.url) == MISSING_URL:
            return httpx.Response(404)
        assert str(request.url) == TV_URL
        raise httpx.ReadTimeout("TV request timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout):
            EdailyRSS(client, "stock").article(missing_entry)


@pytest.mark.parametrize(
    ("tv_status", "tv_body"),
    [(200, "실제 기사 본문"), (404, ""), (503, ""), (200, ""), (200, '<img src="body.jpg">')],
)
def test_edaily_scrape_stores_recovered_url_or_reports_failure(engine, tv_status, tv_body):
    feed = FEED.replace(URL, MISSING_URL)

    def handler(request):
        if str(request.url) == "http://rss.edaily.co.kr/stock_news.xml":
            return httpx.Response(200, text=feed)
        if str(request.url) == MISSING_URL:
            return httpx.Response(404)
        assert str(request.url) == TV_URL
        return httpx.Response(tv_status, text=f'<div class="news_text">{tv_body}</div>')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = scrape(engine, EdailyRSS(client, "stock"))

    with engine.connect() as conn:
        stored = list(
            conn.execute(sa.select(articles.c.external_id, articles.c.url, articles.c.body))
        )
    if tv_status == 200 and tv_body == "실제 기사 본문":
        assert result.succeed == [MISSING_URL]
        assert result.failed == []
        assert stored == [(MISSING_URL, TV_URL, "실제 기사 본문")]
    elif tv_status == 404:
        assert result.succeed == []
        assert result.failed == []
        assert stored == []
    else:
        assert result.succeed == []
        assert result.failed == [MISSING_URL]
        assert stored == []
