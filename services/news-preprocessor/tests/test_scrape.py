import sqlalchemy as sa
from news_preprocessor.scrape import scrape
from news_preprocessor.sources.publishers import HankyungEconomyRSS, MaeilBusinessEconomyRSS
from news_preprocessor.storage import articles

HANKYUNG_ARTICLES = [
    "https://www.hankyung.com/article/202609220001i",
    "https://www.hankyung.com/article/202609220002i",
]


def _sources(engine):
    with engine.connect() as conn:
        query = sa.select(articles.c.source).order_by(articles.c.id)
        return list(conn.execute(query).scalars())


def test_stores_new_articles_and_reports_their_ids(engine, fake_web):
    hankyung, _ = fake_web().sources()

    result = scrape(engine, hankyung)

    assert result.succeed == HANKYUNG_ARTICLES
    assert result.failed == []
    assert _sources(engine) == ["hankyung_economy"] * 2


def test_a_second_scrape_reports_nothing_and_fetches_only_the_feed(engine, fake_web):
    scrape(engine, fake_web().sources()[0])
    web = fake_web()

    result = scrape(engine, web.sources()[0])

    assert result.succeed == []
    assert result.failed == []
    assert len(_sources(engine)) == 2
    assert web.requested == [HankyungEconomyRSS.feed_url]


def test_a_failing_feed_reports_the_feed_url_and_stores_nothing(engine, fake_web):
    web = fake_web(failing_feeds=(MaeilBusinessEconomyRSS.feed_url,))

    result = scrape(engine, web.sources()[1])

    assert result.succeed == []
    assert result.failed == [MaeilBusinessEconomyRSS.feed_url]
    assert _sources(engine) == []


def test_a_failing_article_is_reported_and_the_others_are_stored(engine, fake_web, monkeypatch):
    hankyung, _ = fake_web().sources()
    article = hankyung.article

    def fail_the_first(entry):
        if entry.external_id == HANKYUNG_ARTICLES[0]:
            raise RuntimeError("page unreachable")
        return article(entry)

    monkeypatch.setattr(hankyung, "article", fail_the_first)

    result = scrape(engine, hankyung)

    assert result.succeed == [HANKYUNG_ARTICLES[1]]
    assert result.failed == [HANKYUNG_ARTICLES[0]]
    assert len(_sources(engine)) == 1
