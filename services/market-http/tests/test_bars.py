from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from market_http.app import create_app
from market_http.repositories import bars as bars_module
from redis.asyncio import Redis

from .example_entities import PAGE

NEWEST = datetime(2026, 10, 13, 6, 30, tzinfo=UTC)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def to_pandas(self):
        return pd.DataFrame(self.rows)


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, sql, binds=None):
        self.queries.append((sql, binds))
        return FakeResult(self.rows)


def _rows(count):
    bar = PAGE.items[0]
    return [
        {
            "ts": (NEWEST - timedelta(minutes=offset)).replace(tzinfo=None),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for offset in range(count)
    ]


@pytest.fixture
def client():
    with TestClient(create_app(Redis(host="127.0.0.1", port=1), "conf")) as client:
        yield client


@pytest.mark.parametrize(
    ("timeframe", "view", "session"),
    [("1m", "bars_1m", True), ("15m", "bars_15m", False), ("1h", "bars_1h", False),
     ("1d", "bars_1d", True)],
)  # fmt: skip
def test_bars_reads_the_timeframe_view_newest_first(monkeypatch, client, timeframe, view, session):
    db = FakeDB(_rows(3))
    monkeypatch.setattr(bars_module.questdb, "connect", lambda conf: db)

    response = client.get(f"/bars/005930?timeframe={timeframe}&limit=2")

    sql, binds = db.queries[0]
    assert f"FROM {view} " in sql
    assert ("session = 'regular'" in sql) is session
    assert "ORDER BY ts DESC" in sql
    assert binds == ["005930", 3]
    body = response.json()
    assert [datetime.fromisoformat(item["timestamp"]) for item in body["items"]] == [
        NEWEST,
        NEWEST - timedelta(minutes=1),
    ]
    assert all("ts" not in item for item in body["items"])
    assert body["next_cursor"] is not None


def test_cursor_returns_the_next_older_page(monkeypatch, client):
    first = FakeDB(_rows(3))
    monkeypatch.setattr(bars_module.questdb, "connect", lambda conf: first)
    cursor = client.get("/bars/005930?timeframe=1m&limit=2").json()["next_cursor"]
    second = FakeDB(_rows(1))
    monkeypatch.setattr(bars_module.questdb, "connect", lambda conf: second)

    body = client.get(f"/bars/005930?timeframe=1m&limit=2&cursor={cursor}").json()

    sql, binds = second.queries[0]
    assert "ts < cast($2 AS TIMESTAMP)" in sql
    assert binds == ["005930", "2026-10-13T06:29:00.000000Z", 3]
    assert body["next_cursor"] is None


@pytest.mark.parametrize(
    "query",
    [
        "timeframe=5m",
        "timeframe=1m&cursor=not-base64!",
        "timeframe=1m&cursor=" + urlsafe_b64encode(b"2026-10-13T06:29:00").decode(),
        "timeframe=1m&limit=0",
        "timeframe=1m&limit=1001",
    ],
)
def test_invalid_query_is_rejected(client, query):
    assert client.get(f"/bars/005930?{query}").status_code == 422


def test_malformed_code_is_rejected(client):
    assert client.get("/bars/5930?timeframe=1m").status_code == 422
