import sys
import types
from datetime import UTC, datetime

import pytest
from market_collector.universe.dto import IndexMember
from market_collector.universe.repository import (
    UNIVERSE_MEMBERS_TABLE,
    EmptyUniverseError,
    latest_members,
    upsert_members,
)


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def _member(symbol="005930", index_code="201", stock_name="삼성전자"):
    return IndexMember(index_code=index_code, symbol=symbol, stock_name=stock_name)


def test_upsert_members_writes_one_row_per_member():
    sink = FakeSink()
    ts = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)

    written = upsert_members(sink, ts, [_member("005930"), _member("000660")])

    assert written == 2
    assert {row[1]["symbol"] for row in sink.rows} == {"005930", "000660"}
    assert all(row[0] == UNIVERSE_MEMBERS_TABLE for row in sink.rows)
    assert sink.flushes == 1


def test_upsert_members_records_the_source():
    sink = FakeSink()
    upsert_members(sink, datetime(2026, 9, 25, tzinfo=UTC), [_member()])

    assert sink.rows[0][1]["src"] == "ka20002"


def test_upsert_members_tags_the_index_name_from_the_index_code():
    sink = FakeSink()
    upsert_members(sink, datetime(2026, 9, 25, tzinfo=UTC), [_member(index_code="201")])

    assert sink.rows[0][1]["index_name"] == "KOSPI200"


def test_upsert_members_truncates_the_timestamp_to_the_day():
    # Two runs on the same day must produce the same `at` value, because the
    # QuestDB dedup key is (ts, index_code, symbol) -- this is the
    # precondition that makes a same-day rerun an upsert rather than a
    # second snapshot.
    sink = FakeSink()
    morning = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)
    evening = datetime(2026, 9, 25, 23, 59, tzinfo=UTC)

    upsert_members(sink, morning, [_member()])
    upsert_members(sink, evening, [_member()])

    assert sink.rows[0][3] == sink.rows[1][3] == datetime(2026, 9, 25, tzinfo=UTC)


def test_upsert_members_truncates_a_non_utc_timestamp_after_converting():
    sink = FakeSink()
    from datetime import timedelta, timezone

    kst = timezone(timedelta(hours=9))
    # 2026-09-26 00:30 KST is 2026-09-25 15:30 UTC -- truncating in KST
    # first would give the wrong UTC day.
    late_kst = datetime(2026, 9, 26, 0, 30, tzinfo=kst)

    upsert_members(sink, late_kst, [_member()])

    assert sink.rows[0][3] == datetime(2026, 9, 25, tzinfo=UTC)


def test_latest_members_returns_a_frozenset_of_symbols(monkeypatch):
    calls = {}

    class FakeCursor:
        def execute(self, query, params):
            calls["query"] = str(query)
            calls["params"] = params

        def fetchall(self):
            return [("005930",), ("0126Z0",)]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake_psycopg = types.SimpleNamespace(connect=lambda dsn: FakeConnection())
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    result = latest_members("postgresql://localhost:8812/qdb", "201")

    assert result == frozenset({"005930", "0126Z0"})
    assert isinstance(result, frozenset)
    assert calls["params"] == ("201", "201")
    assert UNIVERSE_MEMBERS_TABLE in calls["query"]


def test_latest_members_raises_empty_universe_error_naming_the_subcommand(monkeypatch):
    class FakeCursor:
        def execute(self, query, params):
            pass

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake_psycopg = types.SimpleNamespace(connect=lambda dsn: FakeConnection())
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    with pytest.raises(EmptyUniverseError, match="universe"):
        latest_members("postgresql://localhost:8812/qdb", "201")
