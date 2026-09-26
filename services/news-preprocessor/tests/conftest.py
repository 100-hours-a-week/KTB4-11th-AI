import pathlib

import httpx
import pytest
import sqlalchemy as sa
from news_preprocessor.sources.publishers import HankyungEconomyRSS, MaeilBusinessEconomyRSS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class FakeWeb:
    def __init__(self, failing_feeds: tuple[str, ...] = ()):
        self.requested: list[str] = []
        self._failing_feeds = failing_feeds

    def client(self, feed_url: str, feed: str, article: str) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            self.requested.append(url)
            if url == feed_url:
                if url in self._failing_feeds:
                    raise httpx.ConnectError("feed unreachable")
                return httpx.Response(200, text=(FIXTURES / feed).read_text(encoding="utf-8"))
            return httpx.Response(200, text=(FIXTURES / article).read_text(encoding="utf-8"))

        return httpx.Client(transport=httpx.MockTransport(handler))

    def sources(self):
        return (
            HankyungEconomyRSS(
                self.client(
                    HankyungEconomyRSS.feed_url, "hankyung_feed.xml", "hankyung_article.html"
                )
            ),
            MaeilBusinessEconomyRSS(
                self.client(
                    MaeilBusinessEconomyRSS.feed_url, "maeil_feed.xml", "maeil_article.html"
                )
            ),
        )


@pytest.fixture
def fake_web():
    return FakeWeb


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE articles RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    # scrape() and embed_pending() commit, so empty the table instead of rolling back.
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)
