import logging

import httpx
import pytest
import sqlalchemy as sa
from news_preprocessor import __main__ as entry
from news_preprocessor import scrape as scraping
from news_preprocessor.embed_pending import EmbedResult
from news_preprocessor.sources.publishers import EdailyRSS

FEED_URL = "http://rss.edaily.co.kr/stock_news.xml"
MISSING_URL = "http://www.edaily.co.kr/news/newspath.asp?newsid=33902086645610296"
FINAL_URL = "https://www.edaily.co.kr/News/Read?newsId=33902086645610296&mediaCodeNo=257"
AVAILABLE_URL = "https://www.edaily.co.kr/News/Read?newsId=05172566645608656"
FEED = f"""<rss><channel>
<item><title>조회 불가 기사</title><link>{MISSING_URL}</link>
<pubDate>Tue, 06 Oct 2026 14:15:00 +0900</pubDate></item>
<item><title>정상 기사</title><link>{AVAILABLE_URL}</link>
<pubDate>Tue, 06 Oct 2026 14:16:00 +0900</pubDate></item>
</channel></rss>"""


@pytest.fixture
def scrape_engine(monkeypatch):
    engine = sa.create_engine("sqlite://")
    monkeypatch.setattr(scraping, "known_external_ids", lambda conn, source, ids: set())
    monkeypatch.setattr(scraping, "insert_new", lambda conn, item: True)
    yield engine
    engine.dispose()


def _source(article_status=200, feed_status=200, timeout=False):
    def handler(request):
        url = str(request.url)
        if url == FEED_URL:
            return httpx.Response(feed_status, text=FEED)
        if url == MISSING_URL:
            return httpx.Response(302, headers={"Location": FINAL_URL})
        if url == FINAL_URL:
            if timeout:
                raise httpx.ReadTimeout("article timed out", request=request)
            return httpx.Response(article_status)
        if url == AVAILABLE_URL:
            return httpx.Response(200, text='<div class="news_body">정상 기사 본문</div>')
        raise AssertionError(f"unexpected request: {request.url}")

    return EdailyRSS(
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True), "stock"
    )


@pytest.mark.parametrize("status", [404, 410])
def test_unavailable_article_is_skipped_and_next_article_is_processed(
    scrape_engine, caplog, status
):
    with caplog.at_level(logging.INFO):
        result = scraping.scrape(scrape_engine, _source(article_status=status))

    assert result.succeed == [AVAILABLE_URL]
    assert result.failed == []
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].fields["url"] == MISSING_URL
    assert warnings[0].fields["final_url"] == FINAL_URL
    assert warnings[0].fields["status"] == status
    summary = next(record for record in caplog.records if record.message == "scrape_complete")
    assert summary.fields["skipped"] == 1
    assert summary.fields["failed"] == 0
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


@pytest.fixture
def run_job(scrape_engine, monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "sqlite://")
    monkeypatch.setattr(entry.sa, "create_engine", lambda dsn: scrape_engine)
    monkeypatch.setattr(entry, "embed_pending", lambda *args: EmbedResult(succeed=[], failed=[]))

    def run(source):
        monkeypatch.setattr(entry, "publishers", lambda client: [source])
        with pytest.raises(SystemExit) as exit_info:
            entry.main()
        return exit_info.value.code

    return run


@pytest.mark.parametrize("status", [404, 410])
def test_unavailable_article_does_not_fail_job(run_job, status):
    assert run_job(_source(article_status=status)) == 0


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_other_article_http_errors_still_fail_job(run_job, status):
    assert run_job(_source(article_status=status)) == 1


@pytest.mark.parametrize("status", [404, 410])
def test_unavailable_feed_still_fails_job(run_job, status):
    assert run_job(_source(feed_status=status)) == 1


def test_article_timeout_still_fails_job(run_job):
    assert run_job(_source(timeout=True)) == 1
