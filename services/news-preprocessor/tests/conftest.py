import pathlib
from urllib.error import URLError

import pytest
import sqlalchemy as sa
from news_preprocessor.sources.publishers import HankyungEconomyRSS, MaeilBusinessEconomyRSS

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class FakeWeb:
    def __init__(self, failing_feeds: tuple[str, ...] = ()):
        self.requested: list[str] = []
        self._failing_feeds = failing_feeds

    def fetcher(self, feed_url: str, feed: str, article: str):
        def fetch(url: str, content_type: str) -> str:
            self.requested.append(url)
            if url == feed_url:
                if url in self._failing_feeds:
                    raise URLError("feed unreachable")
                return (FIXTURES / feed).read_text(encoding="utf-8")
            return (FIXTURES / article).read_text(encoding="utf-8")

        return fetch

    def sources(self):
        return (
            HankyungEconomyRSS(
                fetch=self.fetcher(
                    HankyungEconomyRSS.feed_url, "hankyung_feed.xml", "hankyung_article.html"
                )
            ),
            MaeilBusinessEconomyRSS(
                fetch=self.fetcher(
                    MaeilBusinessEconomyRSS.feed_url, "maeil_feed.xml", "maeil_article.html"
                )
            ),
        )


@pytest.fixture
def fake_web():
    return FakeWeb


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE articles RESTART IDENTITY"))


@pytest.fixture
def engine(pg_engine):
    # scrape() and embed_pending() commit, so empty the table instead of rolling back.
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)
