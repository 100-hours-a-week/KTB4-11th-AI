from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from itertools import count

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from ktb_core.clock_file import write_clock
from news_http.evaluation import create_evaluation_app
from news_http.settings import EvaluationSettings
from pydantic import ValidationError

_ids = count()


def at(day, hour=0):
    return datetime(2026, 7, day, hour, tzinfo=UTC)


def insert(engine, published_at, title="title", body="body", fetched_at=None):
    external_id = f"evaluation-{next(_ids)}"
    with engine.begin() as conn:
        return conn.execute(
            sa.text(
                "INSERT INTO articles"
                " (source, external_id, url, title, body, published_at, raw_payload, fetched_at)"
                " VALUES ('test', :external_id, :url, :title, :body, :published_at, '',"
                " :fetched_at) RETURNING id"
            ),
            {
                "external_id": external_id,
                "url": f"https://example.com/{external_id}",
                "title": title,
                "body": body,
                "published_at": published_at,
                "fetched_at": fetched_at or published_at,
            },
        ).scalar_one()


@pytest.fixture
def clock(tmp_path):
    path = tmp_path / "as_of"
    write_clock(path, at(10, 12))
    return path


def make_client(engine, clock, availability="published"):
    return TestClient(create_evaluation_app(engine, str(clock), availability))


@pytest.fixture
def client(engine, clock):
    with make_client(engine, clock) as client:
        yield client


def titles(response):
    return [item["title"] for item in response.json()["items"]]


def test_articles_end_at_the_clock(engine, client, clock):
    insert(engine, at(2), "too old")
    insert(engine, at(5), "in window")
    insert(engine, at(10, 12), "at the clock")
    insert(engine, at(10, 13), "after the clock")
    response = client.get("/news/articles?days=7")
    assert titles(response) == ["at the clock", "in window"]
    assert response.json()["items"][0]["body"] == "body"
    write_clock(clock, at(11))
    assert titles(client.get("/news/articles?days=7")) == [
        "after the clock",
        "at the clock",
        "in window",
    ]


def test_client_parameters_cannot_reach_past_the_clock(engine, client):
    insert(engine, at(9), "visible")
    insert(engine, at(20), "future")
    future = urlsafe_b64encode(f"{at(31).isoformat()}|999".encode()).decode()
    for query in ("as_of=2026-07-31T00:00:00Z", "until=2026-07-31", f"cursor={future}", "days=90"):
        assert titles(client.get(f"/news/articles?{query}")) == ["visible"]


def test_pages_stay_before_the_clock(engine, client):
    for hour in range(5):
        insert(engine, at(9, hour), f"h{hour}")
    insert(engine, at(11), "future")
    first = client.get("/news/articles?limit=3").json()
    second = client.get(f"/news/articles?limit=3&cursor={first['next_cursor']}").json()
    assert [item["title"] for item in first["items"] + second["items"]] == [
        "h4",
        "h3",
        "h2",
        "h1",
        "h0",
    ]
    assert second["next_cursor"] is None


def test_fetched_availability_hides_late_collection(engine, clock):
    insert(engine, at(9), "on time", fetched_at=at(9, 1))
    insert(engine, at(9), "backfilled", fetched_at=at(15))
    with make_client(engine, clock, "fetched") as client:
        assert titles(client.get("/news/articles")) == ["on time"]
    with make_client(engine, clock) as client:
        assert sorted(titles(client.get("/news/articles"))) == ["backfilled", "on time"]


def test_query_matches_title_or_body_literally(engine, client):
    insert(engine, at(9, 1), "반도체 수출", "body")
    insert(engine, at(9, 2), "other", "HBM 100% 증가")
    insert(engine, at(9, 3), "unrelated", "100 증가")
    assert titles(client.get("/news/articles?q=반도체")) == ["반도체 수출"]
    assert titles(client.get("/news/articles?q=100%25")) == ["other"]
    assert len(titles(client.get("/news/articles?q=%20"))) == 3


@pytest.mark.parametrize(
    "path",
    ["/news/recent", "/clusters", "/clusters/1", "/clusters/1/articles", "/graph/search?q=a"],
)
def test_derived_and_production_routes_do_not_exist(client, path):
    assert client.get(path).status_code == 404


def test_unreadable_clock_fails_closed(engine, client, clock):
    insert(engine, at(9), "visible")
    clock.unlink()
    assert client.get("/news/articles").status_code == 503


def test_settings_require_a_clock(monkeypatch):
    monkeypatch.setenv("NEWS_HTTP_POSTGRES_DSN", "postgresql+psycopg://ktb:ktb@localhost/ktb")
    with pytest.raises(ValidationError):
        EvaluationSettings()
    monkeypatch.setenv("NEWS_HTTP_EVALUATION_CLOCK_FILE", "/clock/as_of")
    assert EvaluationSettings().evaluation_availability == "published"
