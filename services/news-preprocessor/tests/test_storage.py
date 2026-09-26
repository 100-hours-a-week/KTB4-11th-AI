from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from news_preprocessor.sources import NewsItem
from news_preprocessor.storage import (
    articles,
    insert_new,
    known_external_ids,
    pending_embedding,
    set_embedding,
)


@pytest.fixture
def conn(pg_conn):
    pg_conn.execute(sa.text("TRUNCATE articles CASCADE"))
    return pg_conn


def _item(external_id: str, source: str = "hankyung_economy") -> NewsItem:
    return NewsItem(
        source=source,
        external_id=external_id,
        url=external_id,
        title=f"제목 {external_id}",
        published_at=datetime(2026, 9, 22, 6, 0, tzinfo=UTC),
        raw_payload="<item/>",
        body=f"본문 {external_id}",
    )


def _vector(first: float) -> list[float]:
    return [first] + [0.0] * 1999


def test_insert_new_adds_a_row_once(conn):
    assert insert_new(conn, _item("a")) is True
    assert insert_new(conn, _item("a")) is False

    count = conn.execute(sa.select(sa.func.count()).select_from(articles)).scalar()
    assert count == 1


def test_the_same_external_id_from_another_source_is_a_different_article(conn):
    assert insert_new(conn, _item("a", source="hankyung_economy")) is True
    assert insert_new(conn, _item("a", source="maeil_business_economy")) is True


def test_known_external_ids_is_scoped_to_the_source(conn):
    insert_new(conn, _item("a"))
    insert_new(conn, _item("b", source="maeil_business_economy"))

    assert known_external_ids(conn, "hankyung_economy", ["a", "b", "c"]) == {"a"}
    assert known_external_ids(conn, "hankyung_economy", []) == set()


def test_pending_embedding_returns_only_null_rows_in_id_order_up_to_the_limit(conn):
    for external_id in ("a", "b", "c"):
        insert_new(conn, _item(external_id))
    first_id = pending_embedding(conn, limit=10)[0].id
    set_embedding(conn, [first_id], [_vector(1.0)])

    rows = pending_embedding(conn, limit=1)

    assert [(row.title, row.body) for row in rows] == [("제목 b", "본문 b")]
    assert len(pending_embedding(conn, limit=10)) == 2


def test_set_embedding_writes_the_vector(conn):
    insert_new(conn, _item("a"))
    [row] = pending_embedding(conn, limit=10)

    set_embedding(conn, [row.id], [_vector(0.5)])

    stored = conn.execute(sa.select(articles.c.embedding)).scalar_one()
    assert len(stored) == 2000
    assert float(stored[0]) == 0.5


def test_set_embedding_rejects_mismatched_lengths(conn):
    with pytest.raises(ValueError):
        set_embedding(conn, [1, 2], [_vector(1.0)])
