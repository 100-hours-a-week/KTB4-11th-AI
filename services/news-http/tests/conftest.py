from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import count

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from news_http.app import create_app

_external_ids = count()


def _truncate(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text("TRUNCATE articles, clusters, entities, corporations RESTART IDENTITY CASCADE")
        )


@pytest.fixture
def engine(pg_engine):
    _truncate(pg_engine)
    yield pg_engine
    _truncate(pg_engine)


@pytest.fixture
def client(engine):
    with TestClient(create_app(engine)) as client:
        yield client


@pytest.fixture
def at():
    def at(day: int, hour: int = 0, minute: int = 0) -> datetime:
        return datetime(2026, 10, day, hour, minute, tzinfo=UTC)

    return at


class Seed:
    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine

    def article(self, published_at: datetime, title: str = "title") -> int:
        external_id = str(next(_external_ids))
        with self.engine.begin() as conn:
            return conn.execute(
                sa.text(
                    "INSERT INTO articles"
                    " (source, external_id, url, title, body, published_at, raw_payload)"
                    " VALUES ('test', :external_id, :url, :title, 'body', :published_at, '')"
                    " RETURNING id"
                ),
                {
                    "external_id": external_id,
                    "url": f"https://example.com/{external_id}",
                    "title": title,
                    "published_at": published_at,
                },
            ).scalar_one()

    def cluster(self, article_ids: Iterable[int], title: str | None = "title") -> int:
        with self.engine.begin() as conn:
            cluster_id = conn.execute(
                sa.text("INSERT INTO clusters DEFAULT VALUES RETURNING id")
            ).scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO article_clusters (article_id, cluster_id)"
                    " VALUES (:article_id, :cluster_id)"
                ),
                [
                    {"article_id": article_id, "cluster_id": cluster_id}
                    for article_id in article_ids
                ],
            )
            if title is not None:
                conn.execute(
                    sa.text(
                        "INSERT INTO cluster_summaries"
                        " (cluster_id, title, summary, cluster_updated_at)"
                        " VALUES (:cluster_id, :title, :summary, now())"
                    ),
                    {"cluster_id": cluster_id, "title": title, "summary": f"{title} summary"},
                )
            return cluster_id

    def stock(self, stock_code: str) -> int:
        with self.engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO corporations (stock_code, name, corp_code)"
                    " VALUES (:stock_code, :name, :corp_code)"
                ),
                {"stock_code": stock_code, "name": f"name {stock_code}", "corp_code": stock_code},
            )
            return conn.execute(
                sa.text(
                    "INSERT INTO entities (raw_name, name, type, stock_code)"
                    " VALUES (:name, :name, 'company', :stock_code) RETURNING id"
                ),
                {"name": f"name {stock_code}", "stock_code": stock_code},
            ).scalar_one()

    def entity(self, name: str) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                sa.text(
                    "INSERT INTO entities (raw_name, name, type)"
                    " VALUES (:name, :name, 'concept') RETURNING id"
                ),
                {"name": name},
            ).scalar_one()

    def mention(self, cluster_id: int, entity_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO cluster_entities (cluster_id, entity_id)"
                    " VALUES (:cluster_id, :entity_id)"
                ),
                {"cluster_id": cluster_id, "entity_id": entity_id},
            )


@pytest.fixture
def seed(engine):
    return Seed(engine)
