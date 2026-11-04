from base64 import urlsafe_b64encode
from datetime import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from ktb_core.clock_file import write_clock
from ktb_core.krx import KST
from market_http.evaluation import create_evaluation_app
from market_http.repositories import bars as bars_module
from market_http.settings import EvaluationSettings
from pydantic import ValidationError

ROW = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}


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


@pytest.fixture
def db(monkeypatch):
    db = FakeDB([{"timestamp": datetime(2026, 7, 6), **ROW}])
    monkeypatch.setattr(bars_module.questdb, "connect", lambda conf: db)
    return db


@pytest.fixture
def clock(tmp_path):
    path = tmp_path / "as_of"
    write_clock(path, datetime(2026, 7, 7, 8, tzinfo=KST))
    return path


@pytest.fixture
def pit(clock):
    with TestClient(
        create_evaluation_app("conf", str(clock), "http://market-http-pit:8000")
    ) as client:
        yield client


@pytest.mark.parametrize(
    ("as_of", "bound"),
    [
        (datetime(2026, 7, 7, 8, tzinfo=KST), "2026-07-06T00:00:00.000000Z"),
        (datetime(2026, 7, 7, 15, 29, tzinfo=KST), "2026-07-06T00:00:00.000000Z"),
        (datetime(2026, 7, 7, 15, 30, tzinfo=KST), "2026-07-07T00:00:00.000000Z"),
        (datetime(2026, 7, 11, 12, tzinfo=KST), "2026-07-10T00:00:00.000000Z"),
    ],
)
def test_daily_bars_end_at_the_last_close_before_the_clock(db, pit, clock, as_of, bound):
    write_clock(clock, as_of)
    body = pit.get("/bars/005930?timeframe=1d&limit=2").json()
    sql, binds = db.queries[0]
    assert "FROM bars_1d " in sql
    assert "timestamp <= cast($2 AS TIMESTAMP)" in sql
    assert binds == ["005930", bound, 3]
    assert body["next_cursor"] is None


def test_client_parameters_cannot_move_the_cutoff(db, pit):
    future = urlsafe_b64encode(b"2026-12-31T00:00:00+00:00").decode()
    pit.get(f"/bars/005930?timeframe=1d&as_of=2026-12-31T00:00:00Z&until=x&cursor={future}")
    sql, binds = db.queries[0]
    assert binds == [
        "005930",
        "2026-12-31T00:00:00.000000Z",
        "2026-07-06T00:00:00.000000Z",
        301,
    ]
    assert "timestamp < cast($2 AS TIMESTAMP) AND timestamp <= cast($3 AS TIMESTAMP)" in sql


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1h", "5m"])
def test_only_daily_bars_are_served(db, pit, timeframe):
    assert pit.get(f"/bars/005930?timeframe={timeframe}").status_code == 422
    assert db.queries == []


@pytest.mark.parametrize(
    "path",
    ["/prices?codes=005930", "/orderbooks/005930", "/stocks/005930", "/themes", "/rankings"],
)
def test_other_routes_do_not_exist(pit, path):
    assert pit.get(path).status_code == 404


def test_unreadable_clock_fails_closed(db, pit, clock):
    clock.write_text("not a time")
    assert pit.get("/bars/005930?timeframe=1d").status_code == 503
    clock.unlink()
    assert pit.get("/bars/005930?timeframe=1d").status_code == 503
    assert db.queries == []


def test_harness_access_has_no_cutoff(db):
    with TestClient(create_evaluation_app("conf", None)) as client:
        client.get("/bars/005930?timeframe=1d&limit=5")
    sql, binds = db.queries[0]
    assert "timestamp <=" not in sql
    assert binds == ["005930", 6]


def test_settings_tie_the_clock_to_pit_access(monkeypatch):
    monkeypatch.setenv("MARKET_HTTP_QUESTDB_CONF", "conf")
    monkeypatch.setenv("MARKET_HTTP_EVALUATION_ACCESS", "pit")
    with pytest.raises(ValidationError):
        EvaluationSettings()
    monkeypatch.setenv("MARKET_HTTP_EVALUATION_CLOCK_FILE", "/clock/as_of")
    with pytest.raises(ValidationError):
        EvaluationSettings()
    monkeypatch.setenv("MARKET_HTTP_EVALUATION_PUBLIC_URL", "http://market-http-pit:8000")
    assert EvaluationSettings().evaluation_clock_file == "/clock/as_of"
    monkeypatch.setenv("MARKET_HTTP_EVALUATION_ACCESS", "harness")
    with pytest.raises(ValidationError):
        EvaluationSettings()
    monkeypatch.delenv("MARKET_HTTP_EVALUATION_CLOCK_FILE")
    assert EvaluationSettings().evaluation_access == "harness"
    monkeypatch.delenv("MARKET_HTTP_EVALUATION_ACCESS")
    with pytest.raises(ValidationError):
        EvaluationSettings()


def test_openapi_names_the_server_and_the_tool(pit):
    spec = pit.get("/openapi.json").json()
    assert spec["servers"] == [{"url": "http://market-http-pit:8000"}]
    assert [op["operationId"] for path in spec["paths"].values() for op in path.values()] == [
        "get_daily_bars"
    ]
