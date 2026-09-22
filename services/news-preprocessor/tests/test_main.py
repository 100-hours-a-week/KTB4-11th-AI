import pathlib
from urllib.error import URLError

import pytest
import sqlalchemy as sa
from news_preprocessor import __main__ as entry
from news_preprocessor.__main__ import run
from news_preprocessor.sources.publishers import HankyungEconomyRSS, MaeilBusinessEconomyRSS
from news_preprocessor.storage import articles

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
VECTOR = [1.0] + [0.0] * 1999


class FakeWeb:
    """Serves each publisher's feed fixture, and its article fixture for any other URL."""

    def __init__(self, failing_feeds: tuple[str, ...] = ()):
        self.requested: list[str] = []
        self._failing_feeds = failing_feeds

    def fetcher(self, feed_url: str, feed: str, article: str):
        def fetch(url: str) -> bytes:
            self.requested.append(url)
            if url == feed_url:
                if url in self._failing_feeds:
                    raise URLError("feed unreachable")
                return (FIXTURES / feed).read_bytes()
            return (FIXTURES / article).read_bytes()

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


def fake_embedder(texts: list[str]) -> list[list[float]]:
    return [VECTOR for _ in texts]


def failing_embedder(texts: list[str]) -> list[list[float]]:
    raise URLError("embedding host unreachable")


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("TRUNCATE articles RESTART IDENTITY"))


@pytest.fixture
def engine(pg_engine):
    """`run()` commits, so these tests empty `articles` before and after instead of rolling back."""
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)


def _rows(engine):
    with engine.connect() as conn:
        query = sa.select(articles.c.source, articles.c.embedding).order_by(articles.c.id)
        return conn.execute(query).all()


def test_run_stores_and_embeds_articles_from_both_publishers(engine):
    assert run(engine, FakeWeb().sources(), fake_embedder, embed_batch_limit=100) is True

    rows = _rows(engine)
    assert [row.source for row in rows] == ["hankyung_economy"] * 2 + ["maeil_business_economy"] * 2
    assert all(len(row.embedding) == 2000 for row in rows)


def test_second_run_adds_nothing_and_fetches_only_the_feeds(engine):
    run(engine, FakeWeb().sources(), fake_embedder, embed_batch_limit=100)
    web = FakeWeb()

    assert run(engine, web.sources(), fake_embedder, embed_batch_limit=100) is True

    assert len(_rows(engine)) == 4
    assert web.requested == [HankyungEconomyRSS.feed_url, MaeilBusinessEconomyRSS.feed_url]


def test_a_failing_feed_does_not_stop_the_other_publisher(engine):
    web = FakeWeb(failing_feeds=(HankyungEconomyRSS.feed_url,))

    assert run(engine, web.sources(), fake_embedder, embed_batch_limit=100) is False

    assert [row.source for row in _rows(engine)] == ["maeil_business_economy"] * 2


def test_embedding_failure_keeps_articles_pending_until_a_later_run(engine):
    assert run(engine, FakeWeb().sources(), failing_embedder, embed_batch_limit=100) is False
    assert all(row.embedding is None for row in _rows(engine))

    assert run(engine, FakeWeb().sources(), fake_embedder, embed_batch_limit=100) is True
    assert all(row.embedding is not None for row in _rows(engine))


def test_embed_batch_limit_caps_one_run(engine):
    run(engine, FakeWeb().sources(), fake_embedder, embed_batch_limit=3)

    assert sum(row.embedding is not None for row in _rows(engine)) == 3


@pytest.mark.parametrize(("ok", "code"), [(True, 0), (False, 1)])
def test_main_exit_code_reflects_the_run(monkeypatch, ok, code):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "postgresql+psycopg://u@unused.invalid/db")
    monkeypatch.setenv("KTB_EMBEDDING_BASE_URI", "http://embedder:8000/v1")
    monkeypatch.setattr(entry, "run", lambda *args: ok)

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == code
