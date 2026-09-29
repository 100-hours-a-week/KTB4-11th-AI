from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import count

import pytest
import sqlalchemy as sa
from ktb_core.normalize import normalize

_external_ids = count()

TABLES = (
    "relations, cluster_entities, cluster_summaries, entities,"
    " corporation_aliases, corporations, article_clusters, clusters, articles"
)


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    # The code under test commits, so empty the tables instead of rolling back.
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)


def add_article(
    conn: sa.Connection,
    published_at: datetime = datetime(2026, 9, 24, tzinfo=UTC),
    title: str = "title",
    body: str = "body",
) -> int:
    return conn.execute(
        sa.text(
            "INSERT INTO articles"
            " (source, external_id, url, title, body, published_at, raw_payload)"
            " VALUES ('test', :external_id, 'https://example.com', :title, :body,"
            " :published_at, '')"
            " RETURNING id"
        ),
        {
            "external_id": str(next(_external_ids)),
            "title": title,
            "body": body,
            "published_at": published_at,
        },
    ).scalar_one()


def add_cluster(conn: sa.Connection, article_ids: Iterable[int]) -> int:
    cluster_id = conn.execute(
        sa.text("INSERT INTO clusters DEFAULT VALUES RETURNING id")
    ).scalar_one()
    for article_id in article_ids:
        conn.execute(
            sa.text("INSERT INTO article_clusters (article_id, cluster_id) VALUES (:a, :c)"),
            {"a": article_id, "c": cluster_id},
        )
    return cluster_id


@pytest.fixture
def article():
    return add_article


@pytest.fixture
def cluster():
    return add_cluster


def read_updated_at(conn: sa.Connection, cluster_id: int) -> datetime:
    return conn.execute(
        sa.text("SELECT updated_at FROM clusters WHERE id = :id"), {"id": cluster_id}
    ).scalar_one()


@pytest.fixture
def updated_at():
    return read_updated_at


def add_corporation(
    conn: sa.Connection, stock_code: str, name: str, corp_code: str, *other_names: str
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO corporations (stock_code, name, corp_code)"
            " VALUES (:stock_code, :name, :corp_code)"
        ),
        {"stock_code": stock_code, "name": name, "corp_code": corp_code},
    )
    conn.execute(
        sa.text("INSERT INTO corporation_aliases (alias, stock_code) VALUES (:alias, :stock_code)"),
        [{"alias": normalize(alias), "stock_code": stock_code} for alias in (name, *other_names)],
    )


@pytest.fixture
def corporation():
    return add_corporation
