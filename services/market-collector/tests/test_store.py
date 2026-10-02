from contextlib import nullcontext
from datetime import UTC, datetime

import pandas as pd
import pytest
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


class SequencedDatabase(FakeDatabase):
    def __init__(self, answers):
        super().__init__()
        self.answers = [pd.DataFrame(answer) for answer in answers]

    def query(self, sql, binds=None):
        self.queries.append((sql, binds))
        return FakeResult(self.answers.pop(0))


def test_recent_checkpoints_skip_historical_queries_and_bind_current_symbols():
    rows = [
        {"symbol": symbol, "timeframe": timeframe, "latest_ts": TS}
        for symbol in ("005930", "000660")
        for timeframe in ("1m", "1d")
    ]
    db = SequencedDatabase([rows])
    latest = Store(db).latest_bar_timestamps(["005930", "000660", "005930"])
    assert latest == {(r["symbol"], r["timeframe"]): TS for r in rows}
    assert len(db.queries) == 1
    sql, binds = db.queries[0]
    assert binds == ["000660", "005930"]
    assert sql.count("ts > dateadd('d', -7, now())") == 2
    assert sql.count("symbol IN ($1, $2)") == 2


def test_only_missing_symbol_timeframe_pairs_fall_back_to_history(caplog):
    old = TS.replace(year=2025)
    db = SequencedDatabase(
        [
            [
                {"symbol": "active", "timeframe": "1m", "latest_ts": TS},
                {"symbol": "active", "timeframe": "1d", "latest_ts": TS},
                {"symbol": "stale", "timeframe": "1d", "latest_ts": TS},
            ],
            [{"symbol": "stale", "timeframe": "1m", "latest_ts": old}],
            [],
        ]
    )
    with caplog.at_level("INFO"):
        latest = Store(db).latest_bar_timestamps(["active", "stale", "new"])
    assert latest == {
        ("active", "1m"): TS,
        ("active", "1d"): TS,
        ("stale", "1m"): old,
        ("stale", "1d"): TS,
    }
    minute_sql, minute_binds = db.queries[1]
    daily_sql, daily_binds = db.queries[2]
    assert minute_binds == ["new", "stale"]
    assert daily_binds == ["new"]
    assert "WHERE timeframe = '1m'" in minute_sql
    assert "WHERE timeframe = '1d'" not in minute_sql
    assert "WHERE timeframe = '1d'" in daily_sql
    assert "dateadd" not in minute_sql + daily_sql
    assert all(ts.tzinfo is UTC for ts in latest.values())
    complete = next(r for r in caplog.records if r.message == "checkpoint_query_complete")
    assert complete.fields["fallback_pairs"] == 3
    assert complete.fields["missing_pairs"] == 2


def test_empty_target_symbols_do_not_query_questdb():
    db = SequencedDatabase([])
    assert Store(db).latest_bar_timestamps([]) == {}
    assert db.queries == []


def test_historical_query_failure_is_logged_and_propagated(caplog):
    class FailedFallback(SequencedDatabase):
        def query(self, sql, binds=None):
            if "dateadd" not in sql:
                raise RuntimeError("historical query failed")
            return super().query(sql, binds)

    with pytest.raises(RuntimeError, match="historical query failed"):
        Store(FailedFallback([[]])).latest_bar_timestamps(["005930"])
    failed = next(r for r in caplog.records if r.message == "checkpoint_query_failed")
    assert failed.exc_info is not None


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
    assert at == TS
    assert db.output.flushes == 1


@pytest.mark.parametrize("timeframe", ["15m", "1h", "4h"])
def test_derived_timeframes_are_rejected_before_opening_a_sender(timeframe):
    db = FakeDatabase()

    with pytest.raises(KeyError, match=timeframe):
        Store(db).write_candles(timeframe, [_candle()])

    assert db.sender_calls == 0


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
    db = SequencedDatabase([frame.to_dict("records"), []])

    latest = Store(db).latest_bar_timestamps(["005930", "000660"])

    assert latest == {
        ("005930", "1m"): TS,
        ("005930", "1d"): TS.replace(day=21),
        ("000660", "1m"): TS.replace(day=20),
    }
    assert all(timestamp.tzinfo is UTC for timestamp in latest.values())
    sql, binds = db.queries[0]
    assert sql.count("LATEST ON ts PARTITION BY symbol") == 2
    assert "WHERE timeframe = '1m'" in sql
    assert "WHERE timeframe = '1d'" in sql
    assert "UNION ALL" in sql
    assert "max(ts)" not in sql
    assert binds == ["000660", "005930"]


def test_latest_bar_timestamps_returns_empty_boundaries_for_empty_bars():
    assert Store(FakeDatabase()).latest_bar_timestamps(["005930"]) == {}


def test_latest_bar_timestamps_converts_aware_timestamps_to_utc():
    frame = pd.DataFrame(
        {
            "symbol": ["005930"],
            "timeframe": ["1d"],
            "latest_ts": [pd.Timestamp("2026-09-22T15:19:00+09:00")],
        }
    )
    db = SequencedDatabase([frame.to_dict("records"), []])
    assert Store(db).latest_bar_timestamps(["005930"]) == {("005930", "1d"): TS}


def test_latest_bar_timestamps_logs_query_cost(caplog):
    with caplog.at_level("INFO"):
        Store(FakeDatabase()).latest_bar_timestamps(["005930"])
    record = next(r for r in caplog.records if r.message == "checkpoint_query_complete")
    assert record.fields["rows"] == 0
    assert record.fields["elapsed_ms"] >= 0


def test_latest_bar_timestamps_preserves_server_errors_and_logs_cost(caplog):
    class FailedResult(FakeResult):
        def to_pandas(self):
            raise RuntimeError("GC overhead limit exceeded")

    class FailedDatabase(FakeDatabase):
        def query(self, sql, binds=None):
            return FailedResult(self.frame)

    with pytest.raises(RuntimeError, match="GC overhead limit exceeded"):
        Store(FailedDatabase()).latest_bar_timestamps(["005930"])
    record = next(r for r in caplog.records if r.message == "checkpoint_query_failed")
    assert record.fields["elapsed_ms"] >= 0
    assert record.exc_info is not None


def test_without_nones_keeps_falsy_but_non_none_values():
    assert _without_nones({"a": 0.0, "b": False, "c": None, "d": 1, "e": ""}) == {
        "a": 0.0,
        "b": False,
        "d": 1,
        "e": "",
    }
