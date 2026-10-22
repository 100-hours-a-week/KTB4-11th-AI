from contextlib import nullcontext
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from market_collector.popularity import popular_codes

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 15, 9, 1, 5, tzinfo=KST)
DAYS = [datetime(2026, 10, day) for day in (14, 13, 12, 8, 7)]


class FakeDatabase:
    def __init__(self, volumes=(), current=("a",), days=DAYS):
        self.answers = [
            [{"session_day": day} for day in days],
            [{"symbol": symbol} for symbol in current],
            list(volumes),
        ]
        self.queries = []

    def query(self, sql, binds):
        self.queries.append((sql, binds.copy()))
        frame = pd.DataFrame(self.answers.pop(0))
        return nullcontext(type("Result", (), {"to_pandas": lambda _: frame})())


@pytest.mark.parametrize(
    ("hour", "minute", "expected_start", "expected_end"),
    [(9, 1, (8, 59), (9, 0)), (15, 35, (15, 0), (15, 30))],
)
def test_windows_use_completed_minutes_and_clip_to_regular_hours(
    hour, minute, expected_start, expected_end
):
    db = FakeDatabase()
    popular_codes(db, ["a", "b"], NOW.replace(hour=hour, minute=minute))
    dates_sql, dates_binds = db.queries[0]
    assert "ORDER BY session_day DESC LIMIT 5" in dates_sql
    assert dates_binds == ["a", "b", "2026-10-14T15:00:00.000000Z"]
    for sql, binds in db.queries:
        assert "timeframe = '1m'" in sql
        assert "session = 'regular'" in sql
        assert "symbol IN ($1, $2)" in sql
        assert binds[:2] == ["a", "b"]
    sql, binds = db.queries[2]
    assert "sum(volume)" in sql
    assert "GROUP BY symbol, session_day" in sql
    windows = [datetime.fromisoformat(value).astimezone(KST) for value in binds[2:]]
    assert len(windows) == 12
    assert [value.day for value in windows[::2]] == [15, 14, 13, 12, 8, 7]
    assert all((value.hour, value.minute) == expected_start for value in windows[::2])
    assert all((value.hour, value.minute) == expected_end for value in windows[1::2])


def test_opening_pass_does_not_query_forming_bar():
    db = FakeDatabase()
    assert popular_codes(db, ["a"], NOW.replace(minute=0)) == []
    assert db.queries == []


def test_incomplete_history_uses_three_available_session_mean():
    volumes = [
        {"symbol": "a", "session_day": NOW.replace(tzinfo=None), "volume": 100},
        {"symbol": "b", "session_day": NOW.replace(tzinfo=None), "volume": 250},
        *[{"symbol": "a", "session_day": day, "volume": 50} for day in DAYS[:3]],
        *[{"symbol": "b", "session_day": day, "volume": 100} for day in DAYS],
    ]
    assert popular_codes(FakeDatabase(volumes, current=("a", "b")), ["a", "b"], NOW) == [
        "b",
        "a",
    ]


def test_relative_volume_ties_fallbacks_and_zero_current_order():
    totals = {
        "ratio": (100, 10),
        "tie_big": (200, 100),
        "tie_a": (100, 50),
        "tie_b": (100, 50),
        "absolute": (1000, 1000),
        "new": (9000, None),
        "zero_history": (8000, 0),
        "zero_current_a": (0, 100),
        "zero_current_b": (0, None),
        "historical_only": (None, 50),
    }
    rows = []
    for symbol, (current, historical) in totals.items():
        if current is not None:
            rows.append({"symbol": symbol, "session_day": NOW, "volume": current})
        if historical is not None:
            rows.append({"symbol": symbol, "session_day": DAYS[0], "volume": historical})
    symbols = [*totals, "unsupported"]
    db = FakeDatabase(rows, current=[s for s, (v, _) in totals.items() if v is not None])
    assert popular_codes(db, symbols, NOW) == [
        "ratio",
        "tie_big",
        "tie_a",
        "tie_b",
        "absolute",
        "new",
        "zero_history",
        "historical_only",
        "zero_current_a",
        "zero_current_b",
    ]


def test_full_universe_is_ranked_without_whole_market_filter():
    symbols = [f"{i:06}" for i in range(100)]
    rows = [{"symbol": symbol, "session_day": NOW, "volume": i} for i, symbol in enumerate(symbols)]
    assert popular_codes(FakeDatabase(rows, current=symbols), symbols, NOW) == symbols[::-1]


def test_no_completed_current_session_bars_returns_no_ranking():
    db = FakeDatabase(current=())
    assert popular_codes(db, ["a"], NOW) == []
    assert len(db.queries) == 2


def test_completed_current_bar_outside_window_is_eligible_with_zero_volume():
    rows = [{"symbol": "b", "session_day": NOW, "volume": 10}]
    db = FakeDatabase(rows, current=("a", "b"))
    assert popular_codes(db, ["a", "b"], NOW.replace(hour=12)) == ["b", "a"]


def test_empty_universe_does_not_query():
    db = FakeDatabase()
    assert popular_codes(db, [], NOW) == []
    assert db.queries == []


def test_questdb_errors_propagate():
    class FailedDatabase:
        def query(self, sql, binds):
            raise RuntimeError("QuestDB unavailable")

    with pytest.raises(RuntimeError, match="QuestDB unavailable"):
        popular_codes(FailedDatabase(), ["a"], NOW)
