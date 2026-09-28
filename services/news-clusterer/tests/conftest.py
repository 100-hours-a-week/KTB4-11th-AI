from datetime import UTC, datetime
from itertools import count

import pytest
import sqlalchemy as sa

_external_ids = count()


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text("TRUNCATE article_clusters, clusters, articles RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def engine(pg_engine):
    # The code under test commits, so empty the tables instead of rolling back.
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)


def add_article(
    conn: sa.Connection,
    embedding: list[float] | None = None,
    published_at: datetime = datetime(2026, 9, 24, tzinfo=UTC),
    title: str = "title",
    body: str = "body",
) -> int:
    return conn.execute(
        sa.text(
            "INSERT INTO articles"
            " (source, external_id, url, title, body, published_at, raw_payload, embedding)"
            " VALUES ('test', :external_id, 'https://example.com', :title, :body,"
            " :published_at, '', CAST(:embedding AS vector))"
            " RETURNING id"
        ),
        {
            "external_id": str(next(_external_ids)),
            "title": title,
            "body": body,
            "published_at": published_at,
            "embedding": None if embedding is None else "[" + ",".join(map(str, embedding)) + "]",
        },
    ).scalar_one()


@pytest.fixture
def article():
    return add_article
