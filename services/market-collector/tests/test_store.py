from contextlib import nullcontext
from datetime import UTC, datetime

import pandas as pd
import pytest
from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import CandleRow, Store, _without_nones

TS = datetime(2026, 9, 22, 6, 19, tzinfo=UTC)


class FakeSender:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


class FakeResult:
    def __init__(self, frame):
        self.frame = frame

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def to_pandas(self):
        return self.frame


class FakeDatabase:
    def __init__(self, frame=None):
        self.output = FakeSender()
        self.frame = pd.DataFrame() if frame is None else frame
        self.queries = []
        self.sender_calls = 0

    def sender(self):
        self.sender_calls += 1
        return nullcontext(self.output)

    def query(self, sql, binds=None):
        self.queries.append((sql, binds))
        return FakeResult(self.frame)


def _candle(**changes) -> CandleRow:
    values = {
        "ts": TS,
        "symbol": "005930",
        "session": "regular",
        "open": 277750.0,
        "high": 278000.0,
        "low": 277500.0,
        "close": 277500.0,
        "volume": 38961,
        "src": "rest",
    }
    values.update(changes)
    return CandleRow(**values)


@pytest.mark.parametrize("timeframe", ["1m", "1d"])
def test_physical_timeframes_write_ohlcv_to_bars(timeframe):
    db = FakeDatabase()
    candle = _candle()

    assert not hasattr(candle, "trade_value")
    assert Store(db).write_candles(timeframe, [candle]) == 1

    table, symbols, columns, at = db.output.rows[0]
    assert table == "bars"
    assert symbols == {"symbol": "005930", "session": "regular", "src": "rest"}
    assert columns == {
        "timeframe": timeframe,
        "open": 277750.0,
        "high": 278000.0,
        "low": 277500.0,
        "close": 277500.0,
        "volume": 38961,
    }
    assert "trade_value" not in columns
    assert at == TS
    assert db.output.flushes == 1


@pytest.mark.parametrize("timeframe", ["15m", "1h", "4h"])
def test_derived_timeframes_are_rejected_before_opening_a_sender(timeframe):
    db = FakeDatabase()

    with pytest.raises(KeyError, match=timeframe):
        Store(db).write_candles(timeframe, [_candle()])

    assert db.sender_calls == 0


def test_theme_groups_and_members_use_the_official_sender():
    db = FakeDatabase()
    store = Store(db)
    group = ThemeGroup("103", "태양광", 10, 297.1, -1.2, 3, 1, 2, "한화솔루션")
    members = [ThemeMember("103", "005930", "삼성전자"), ThemeMember("103", "033170", "시그네틱스")]

    assert store.write_theme_groups(TS, [group]) == 1
    assert store.write_theme_members(TS, members, frozenset({"005930"})) == 1

    assert [row[0] for row in db.output.rows] == ["theme_snapshot", "theme_members"]
    assert db.output.rows[1][1]["symbol"] == "005930"
    assert db.output.flushes == 2


def test_latest_members_reads_the_latest_snapshot_with_bound_index_code():
    db = FakeDatabase(pd.DataFrame({"symbol": ["005930", "000660"]}))

    assert Store(db).latest_members("201") == frozenset({"005930", "000660"})

    sql, binds = db.queries[0]
    assert "universe_members" in sql
    assert binds == ["201"]


def test_read_regular_candles_returns_oldest_first():
    later = TS.replace(minute=20)
    frame = pd.DataFrame(
        {"ts": [later, TS], "high": [2.0, 1.0], "low": [1.0, 0.0], "close": [1.5, 0.5]}
    )
    db = FakeDatabase(frame)

    candles = Store(db).read_regular_candles("1m", "005930", 2)

    assert [candle.ts for candle in candles] == [TS, later]
    assert db.queries[0][1] == ["005930", "1m", 2]


def test_latest_bar_timestamps_groups_physical_rows_into_reconciliation_boundaries():
    naive_ts = TS.replace(tzinfo=None)
    frame = pd.DataFrame(
        {
            "symbol": ["005930", "005930", "000660"],
            "timeframe": ["1m", "1d", "1m"],
            "latest_ts": [
                naive_ts,
                naive_ts.replace(day=21),
                naive_ts.replace(day=20),
            ],
        }
    )
    db = FakeDatabase(frame)

    latest = Store(db).latest_bar_timestamps()

    assert latest == {
        ("005930", "1m"): TS,
        ("005930", "1d"): TS.replace(day=21),
        ("000660", "1m"): TS.replace(day=20),
    }
    assert all(timestamp.tzinfo is UTC for timestamp in latest.values())
    sql, binds = db.queries[0]
    assert "max(ts)" in sql
    assert "GROUP BY symbol, timeframe" in sql
    assert "timeframe IN ('1m', '1d')" in sql
    assert binds is None


def test_without_nones_keeps_falsy_but_non_none_values():
    assert _without_nones({"a": 0.0, "b": False, "c": None, "d": 1, "e": ""}) == {
        "a": 0.0,
        "b": False,
        "d": 1,
        "e": "",
    }
