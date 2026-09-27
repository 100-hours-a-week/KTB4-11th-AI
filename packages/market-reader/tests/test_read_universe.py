import sys
import types

import pytest
from ktb_market_reader import EmptyUniverseError, latest_members
from ktb_market_reader.questdb import UNIVERSE_MEMBERS_TABLE


def _fake_psycopg(rows, seen=None):
    class FakeCursor:
        def execute(self, query, params):
            if seen is not None:
                seen["query"], seen["params"] = str(query), params

        def fetchall(self):
            return rows

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

    return types.SimpleNamespace(connect=lambda dsn: FakeConnection())


def test_latest_members_returns_a_frozenset_of_symbols(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg([("005930",), ("0126Z0",)], seen))

    result = latest_members("postgresql://localhost:8812/qdb", "201")

    assert result == frozenset({"005930", "0126Z0"})
    assert seen["params"] == ("201", "201")
    assert UNIVERSE_MEMBERS_TABLE in str(seen["query"])


def test_latest_members_raises_empty_universe_error_naming_the_subcommand(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg([]))

    with pytest.raises(EmptyUniverseError, match="universe"):
        latest_members("postgresql://localhost:8812/qdb", "201")
