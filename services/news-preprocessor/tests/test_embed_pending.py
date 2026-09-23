from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
from news_preprocessor.embed_pending import embed_pending
from news_preprocessor.sources import NewsItem
from news_preprocessor.storage import articles, insert_new

VECTOR = [1.0] + [0.0] * 1999


def _url(number: int) -> str:
    return f"https://example.test/{number}"


def fake_embedder(texts: list[str]) -> list[list[float]]:
    return [VECTOR for _ in texts]


def failing_embedder(texts: list[str]) -> list[list[float]]:
    raise httpx.ConnectError("embedding host unreachable")


def _insert(engine, count: int) -> None:
    with engine.begin() as conn:
        for number in range(count):
            insert_new(
                conn,
                NewsItem(
                    source="hankyung_economy",
                    external_id=_url(number),
                    url=_url(number),
                    title=f"제목 {number}",
                    published_at=datetime(2026, 9, 22, tzinfo=UTC),
                    raw_payload="<item/>",
                    body=f"본문 {number}",
                ),
            )


def _embeddings(engine):
    with engine.connect() as conn:
        query = sa.select(articles.c.embedding).order_by(articles.c.id)
        return list(conn.execute(query).scalars())


def test_embeds_every_pending_article_and_reports_their_ids(engine):
    _insert(engine, 20)

    result = embed_pending(engine, fake_embedder, limit=100)

    assert result.succeed == [_url(number) for number in range(20)]
    assert result.failed == []
    assert all(len(vector) == 2000 for vector in _embeddings(engine))


def test_sends_title_and_body_together(engine):
    _insert(engine, 1)
    sent = []

    embed_pending(engine, lambda texts: sent.extend(texts) or fake_embedder(texts), limit=100)

    assert sent == ["제목 0\n\n본문 0"]


def test_failure_reports_the_pending_ids_until_a_later_run(engine):
    _insert(engine, 3)

    result = embed_pending(engine, failing_embedder, limit=100)

    assert result.succeed == []
    assert result.failed == [_url(number) for number in range(3)]
    assert _embeddings(engine) == [None, None, None]

    assert embed_pending(engine, fake_embedder, limit=100).failed == []
    assert all(vector is not None for vector in _embeddings(engine))


def test_limit_caps_one_run(engine):
    _insert(engine, 5)

    result = embed_pending(engine, fake_embedder, limit=3)

    assert len(result.succeed) == 3
    assert sum(vector is not None for vector in _embeddings(engine)) == 3
