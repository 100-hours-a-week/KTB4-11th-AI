import sqlalchemy as sa
from news_preprocessor.scrape import scrape
from news_preprocessor.sources.publishers import HankyungEconomyRSS, MaeilBusinessEconomyRSS
from news_preprocessor.storage import articles


def _sources(engine):
    with engine.connect() as conn:
        query = sa.select(articles.c.source).order_by(articles.c.id)
        return list(conn.execute(query).scalars())


def test_stores_new_articles_from_a_publisher(engine, fake_web):
    hankyung, _ = fake_web().sources()

    assert scrape(engine, hankyung) is True

    assert _sources(engine) == ["hankyung_economy"] * 2


def test_a_second_scrape_adds_nothing_and_fetches_only_the_feed(engine, fake_web):
    scrape(engine, fake_web().sources()[0])
    web = fake_web()

    assert scrape(engine, web.sources()[0]) is True

    assert len(_sources(engine)) == 2
    assert web.requested == [HankyungEconomyRSS.feed_url]


def test_a_failing_feed_returns_false_and_stores_nothing(engine, fake_web):
    web = fake_web(failing_feeds=(MaeilBusinessEconomyRSS.feed_url,))

    assert scrape(engine, web.sources()[1]) is False

    assert _sources(engine) == []
