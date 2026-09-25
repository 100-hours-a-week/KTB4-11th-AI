from datetime import UTC, datetime

import pytest
from market_collector.universe.dto import IndexMember
from market_collector.universe.repository import UNIVERSE_MEMBERS_TABLE, EmptyUniverseError
from market_collector.universe.service import sync_universe

TS = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def _member(symbol):
    return IndexMember(index_code="201", symbol=symbol, stock_name=f"name-{symbol}")


def test_sync_universe_writes_every_member():
    sink = FakeSink()

    written = sync_universe(sink, TS, "201", [_member("005930"), _member("000660")])

    assert written == 2
    assert len(sink.rows) == 2
    assert all(row[0] == UNIVERSE_MEMBERS_TABLE for row in sink.rows)


def test_sync_universe_raises_rather_than_writing_an_empty_snapshot():
    sink = FakeSink()

    with pytest.raises(EmptyUniverseError):
        sync_universe(sink, TS, "201", [])

    assert sink.rows == []
    assert sink.flushes == 0
