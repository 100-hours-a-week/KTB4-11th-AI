import sys
import types
from datetime import UTC, datetime, timedelta

import pytest
from market_collector.indicators import INDICATOR_FIELDS
from market_collector.kiwoom.themes import ThemeGroup, ThemeMember
from market_collector.store import (
    TIMEFRAME_TABLES,
    Candle,
    CandleRow,
    EmptyThemeSnapshotError,
    Store,
    _without_nones,
    read_regular_candles,
    read_symbol_themes,
    read_themes,
)

TS = datetime(2026, 9, 22, 6, 19, tzinfo=UTC)


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def _candle(
    *,
    ts: datetime = TS,
    symbol: str = "005930",
    session: str = "regular",
    open: float = 277750.0,
    high: float = 278000.0,
    low: float = 277500.0,
    close: float = 277500.0,
    volume: int = 38961,
    trade_value: float | None = None,
    indicators: dict[str, float | None] | None = None,
    src: str = "rest",
) -> CandleRow:
    return CandleRow(
        ts=ts,
        symbol=symbol,
        session=session,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        trade_value=trade_value,
        indicators=dict.fromkeys(INDICATOR_FIELDS, 1.0) if indicators is None else indicators,
        src=src,
    )


def test_every_timeframe_maps_to_a_table():
    assert TIMEFRAME_TABLES == {
        "1m": "bars_1m",
        "15m": "bars_15m",
        "1h": "bars_1h",
        "1d": "bars_1d",
    }


def test_candle_writes_split_symbols_from_columns():
    sink = FakeSink()

    written = Store(sink).write_candles("1m", [_candle()])

    assert written == 1
    table, symbols, columns, at = sink.rows[0]
    assert table == "bars_1m"
    assert symbols["symbol"] == "005930"
    assert symbols["session"] == "regular"
    assert symbols["src"] == "rest"
    assert columns["open"] == 277750.0
    assert columns["close"] == 277500.0
    assert columns["volume"] == 38961
    assert at == TS


def test_none_valued_columns_are_omitted_so_questdb_stores_null():
    sink = FakeSink()
    indicators = dict.fromkeys(INDICATOR_FIELDS, None)
    indicators["rsi"] = 55.5

    Store(sink).write_candles("1m", [_candle(indicators=indicators, trade_value=None)])

    _, _, columns, _ = sink.rows[0]
    assert columns["rsi"] == 55.5
    assert "macd" not in columns
    assert "trade_value" not in columns


def test_extended_rows_carry_ohlcv_and_no_indicators():
    sink = FakeSink()
    row = _candle(
        session="extended",
        indicators=dict.fromkeys(INDICATOR_FIELDS, None),
    )

    Store(sink).write_candles("1m", [row])

    _, symbols, columns, _ = sink.rows[0]
    assert symbols["session"] == "extended"
    assert columns["close"] == 277500.0
    assert not any(field in columns for field in INDICATOR_FIELDS)


def test_an_unknown_timeframe_is_rejected_before_any_write():
    sink = FakeSink()

    with pytest.raises(KeyError, match="4h"):
        Store(sink).write_candles("4h", [_candle()])

    assert sink.rows == []


def test_writes_are_flushed_once_per_batch():
    sink = FakeSink()

    Store(sink).write_candles("1m", [_candle(), _candle()])

    assert len(sink.rows) == 2
    assert sink.flushes == 1


def test_theme_groups_are_written_with_date_tp_in_the_columns():
    sink = FakeSink()
    group = ThemeGroup(
        code="103",
        name="태양광_발전/설치/운영",
        date_tp=10,
        dt_prft_rt=297.10,
        change_rate=-1.20,
        stock_count=3,
        rising_count=1,
        falling_count=2,
        main_stocks="에스에너지, 한화솔루션",
    )

    Store(sink).write_theme_groups(TS, [group])

    table, symbols, columns, at = sink.rows[0]
    assert table == "theme_snapshot"
    assert symbols == {"theme_code": "103", "theme_name": "태양광_발전/설치/운영"}
    assert columns["date_tp"] == 10
    assert columns["dt_prft_rt"] == 297.10
    assert columns["stock_count"] == 3
    assert columns["main_stocks"] == "에스에너지, 한화솔루션"
    assert at == TS


def test_theme_members_outside_the_universe_are_not_written():
    sink = FakeSink()
    members = [
        ThemeMember(theme_code="557", symbol="005930", stock_name="삼성전자"),
        ThemeMember(theme_code="557", symbol="033170", stock_name="시그네틱스"),
    ]

    written = Store(sink).write_theme_members(TS, members, frozenset({"005930"}))

    assert written == 1
    assert [row[1]["symbol"] for row in sink.rows] == ["005930"]
    assert sink.rows[0][0] == "theme_members"
    # Symbols, no fields. The membership is the whole fact, and QuestDB stores
    # a symbols-only row because the symbols are the series key.
    assert sink.rows[0][2] == {}


def test_read_regular_candles_returns_the_stored_indicator_values(monkeypatch):
    rows = [_db_row(TS, 278000.0, 277500.0, 277500.0, rsi=61.5)]
    calls: dict[str, object] = {}

    class FakeCursor:
        def execute(self, query, params):
            calls["query"] = query
            calls["params"] = params

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

    fake_psycopg = types.SimpleNamespace(connect=lambda dsn: FakeConnection())
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930")

    assert result == [_candle_of(rows[0])]
    # Returned as stored; nothing recomputes it.
    assert result[0].indicators["rsi"] == 61.5
    # A null column reads back as None, the normal state for backfilled history.
    assert result[0].indicators["macd"] is None
    query = str(calls["query"]).lower()
    assert "select ts, high, low, close, " + ", ".join(INDICATOR_FIELDS).lower() in query
    assert "bars_1m" in query
    assert calls["params"] == ("005930",)


def test_read_regular_candles_rejects_unknown_timeframe():
    with pytest.raises(KeyError, match="4h"):
        read_regular_candles("postgresql://localhost:8812/qdb", "4h", "005930")


def _fake_psycopg(rows: list[tuple]):
    """A ``psycopg`` stand-in whose fake cursor behaves like a real
    ``ORDER BY`` / ``LIMIT`` query over ``rows`` -- filtering by ``ts >=``,
    sorting ascending or descending, and truncating to a limit, each only if
    the executed query text asks for it. Tests built on this exercise the
    function's actual SQL choices (ASC vs DESC, whether LIMIT/ts>= appear at
    all) rather than merely pinning query text.
    """

    class FakeCursor:
        def execute(self, query, params) -> None:
            query_l = str(query).lower()
            params_iter = iter(params)
            next(params_iter)  # symbol; every row here already matches it
            result = list(rows)
            if "ts >=" in query_l:
                since = next(params_iter)
                result = [row for row in result if row[0] >= since]
            result.sort(key=lambda row: row[0], reverse="desc" in query_l)
            if "limit" in query_l:
                limit = next(params_iter)
                result = result[:limit]
            self._result = result

        def fetchall(self):
            return self._result

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


def _db_row(ts, high, low, close, *, rsi=55.0):
    """One row as the driver returns it: ts, prices, then the eight indicators."""
    return (ts, high, low, close, rsi, *[None] * (len(INDICATOR_FIELDS) - 1))


def _candle_of(row) -> Candle:
    return Candle(
        ts=row[0],
        high=row[1],
        low=row[2],
        close=row[3],
        indicators=dict(zip(INDICATOR_FIELDS, row[4:], strict=True)),
    )


_FIVE_ROWS = [
    _db_row(datetime(2026, 9, 22, hour, tzinfo=UTC), float(i), float(i) - 1, float(i))
    for i, hour in enumerate(range(9, 14))
]


def test_read_regular_candles_limit_selects_the_newest_not_the_oldest(monkeypatch):
    # Five distinct timestamps: the two oldest and two newest are disjoint
    # sets, so a naive `ORDER BY ts ASC LIMIT 2` (which returns the two
    # oldest) fails this assertion; only selecting the newest two and
    # returning them oldest-first satisfies it.
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=2)

    assert result == [_candle_of(_FIVE_ROWS[3]), _candle_of(_FIVE_ROWS[4])]


def test_read_regular_candles_since_is_inclusive(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles(
        "postgresql://localhost:8812/qdb", "1m", "005930", since=_FIVE_ROWS[2][0]
    )

    assert result == [_candle_of(r) for r in _FIVE_ROWS[2:]]


def test_read_regular_candles_since_and_limit_combine_to_newest_after_since(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles(
        "postgresql://localhost:8812/qdb",
        "1m",
        "005930",
        since=_FIVE_ROWS[1][0],
        limit=2,
    )

    # since keeps rows 1..4; the newest 2 of those are rows 3 and 4.
    assert result == [_candle_of(_FIVE_ROWS[3]), _candle_of(_FIVE_ROWS[4])]


def test_read_regular_candles_since_past_the_newest_row_returns_empty(monkeypatch):
    # An empty list here means "no candles in that window", which is a real
    # answer the caller must handle -- not an error. It also pins that the
    # since clause is spliced into the query at all: a dropped `ts >=` would
    # return all five rows.
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles(
        "postgresql://localhost:8812/qdb",
        "1m",
        "005930",
        since=_FIVE_ROWS[-1][0] + timedelta(hours=1),
    )

    assert result == []


def test_read_regular_candles_limit_beyond_the_row_count_returns_every_row(monkeypatch):
    # Asking for more than exists is not an error, and the rows must still
    # arrive oldest-first -- the DESC-then-reverse path runs here exactly as
    # it does for a limit that truncates, so a missing reverse() would show
    # up as the whole series backwards.
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg(_FIVE_ROWS))

    result = read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=500)

    assert result == [_candle_of(r) for r in _FIVE_ROWS]


def test_read_regular_candles_rejects_non_positive_limit():
    with pytest.raises(ValueError, match="0"):
        read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=0)
    with pytest.raises(ValueError, match="-1"):
        read_regular_candles("postgresql://localhost:8812/qdb", "1m", "005930", limit=-1)


def test_without_nones_keeps_falsy_but_non_none_values():
    # This is the filter that protects a genuine 0.0 MACD/ROC value — and any
    # falsy value a later column introduces — from being silently dropped
    # instead of stored. `is not None` is
    # correct; `if value` (a plausible future "simplification") is not —
    # this test fails against that simplification because 0.0 and False are
    # falsy but must still survive.
    result = _without_nones({"a": 0.0, "b": False, "c": None, "d": 1, "e": ""})

    assert result == {"a": 0.0, "b": False, "d": 1, "e": ""}


def test_zero_valued_indicators_survive_the_write_not_just_none_ones():
    sink = FakeSink()
    indicators = dict.fromkeys(INDICATOR_FIELDS, None)
    indicators["macd"] = 0.0
    indicators["roc"] = 0.0

    Store(sink).write_candles("1m", [_candle(indicators=indicators)])

    _, _, columns, _ = sink.rows[0]
    assert columns["macd"] == 0.0
    assert columns["roc"] == 0.0


def _fake_theme_psycopg(snapshots, members):
    """A ``psycopg`` stand-in that answers the two queries ``read_themes`` makes.

    Told apart by which table the query names, so the test exercises the real
    query text rather than call order.
    """

    class FakeCursor:
        def execute(self, query, params=None):
            self._rows = snapshots if "theme_snapshot" in str(query) else members

        def fetchall(self):
            return self._rows

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


def _snapshot(code="557", name="2차전지", rate=12.5, count=30):
    return (code, name, 5, rate, 1.2, count, 20, 10, "삼성SDI, LG화학")


DSN = "postgresql://localhost:8812/qdb"


def test_read_themes_attaches_each_themes_members(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        _fake_theme_psycopg(
            [_snapshot("557"), _snapshot("103", name="반도체", rate=3.0)],
            [
                ("557", "006400", "삼성SDI"),
                ("557", "051910", "LG화학"),
                ("103", "005930", "삼성전자"),
            ],
        ),
    )

    themes = read_themes(DSN, 5)

    by_code = {t.theme_code: t for t in themes}
    assert by_code["557"].theme_name == "2차전지"
    assert by_code["557"].members == (("006400", "삼성SDI"), ("051910", "LG화학"))
    assert by_code["103"].members == (("005930", "삼성전자"),)


def test_read_themes_orders_by_kiwooms_rating_with_unknowns_last(monkeypatch):
    # The negative rate is what makes "unknown last" testable. Coercing None to
    # 0.0 and sorting on that alone would place the unknown theme *above* the
    # falling one, which is a different claim: "we do not know" is not "flat".
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        _fake_theme_psycopg(
            [
                _snapshot("a", rate=1.0),
                _snapshot("b", rate=None),
                _snapshot("c", rate=99.0),
                _snapshot("d", rate=-5.0),
            ],
            [],
        ),
    )

    codes = [t.theme_code for t in read_themes(DSN, 5)]

    # QuestDB has no NULLS LAST, which is why this ordering is applied in Python.
    assert codes == ["c", "a", "d", "b"]


def test_read_themes_limit_keeps_the_highest_rated(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        _fake_theme_psycopg([_snapshot("a", rate=1.0), _snapshot("c", rate=99.0)], []),
    )

    assert [t.theme_code for t in read_themes(DSN, 5, limit=1)] == ["c"]


def test_read_themes_keeps_a_theme_with_no_stored_members(monkeypatch):
    # Kiwoom's figures cover the whole market, so a theme none of whose members
    # are in the KOSPI 200 still says something. Dropping it would hide that.
    monkeypatch.setitem(sys.modules, "psycopg", _fake_theme_psycopg([_snapshot("557")], []))

    themes = read_themes(DSN, 5)

    assert len(themes) == 1
    assert themes[0].members == ()
    assert themes[0].stock_count == 30


def test_read_themes_raises_when_nothing_was_collected(monkeypatch):
    # An empty list would read as "no themes moved" -- a claim about the market
    # rather than about the collector never having run.
    monkeypatch.setitem(sys.modules, "psycopg", _fake_theme_psycopg([], []))

    with pytest.raises(EmptyThemeSnapshotError, match="themes"):
        read_themes(DSN, 5)


def test_read_themes_rejects_a_non_positive_limit():
    with pytest.raises(ValueError, match="0"):
        read_themes(DSN, 5, limit=0)


def _two_themes():
    return _fake_theme_psycopg(
        [_snapshot("557", rate=12.5), _snapshot("103", name="반도체", rate=99.0)],
        [
            ("557", "006400", "삼성SDI"),
            ("557", "005930", "삼성전자"),
            ("103", "005930", "삼성전자"),
            ("103", "000660", "SK하이닉스"),
        ],
    )


def test_read_symbol_themes_returns_every_theme_holding_the_symbol(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _two_themes())

    themes = read_symbol_themes(DSN, "005930", 5)

    # Highest-rated first, inherited from read_themes.
    assert [t.theme_code for t in themes] == ["103", "557"]


def test_read_symbol_themes_keeps_the_peers_in_each_theme(monkeypatch):
    # The other members are usually the point of asking.
    monkeypatch.setitem(sys.modules, "psycopg", _two_themes())

    themes = read_symbol_themes(DSN, "005930", 5)

    peers = {t.theme_code: sorted(s for s, _ in t.members if s != "005930") for t in themes}
    assert peers == {"103": ["000660"], "557": ["006400"]}


def test_read_symbol_themes_excludes_themes_the_symbol_is_not_in(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _two_themes())

    assert [t.theme_code for t in read_symbol_themes(DSN, "000660", 5)] == ["103"]


def test_a_symbol_in_no_theme_returns_an_empty_list(monkeypatch):
    # An ordinary answer, not an error: plenty of constituents belong to none.
    monkeypatch.setitem(sys.modules, "psycopg", _two_themes())

    assert read_symbol_themes(DSN, "999999", 5) == []


def test_read_symbol_themes_still_raises_when_nothing_was_collected(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_theme_psycopg([], []))

    with pytest.raises(EmptyThemeSnapshotError, match="themes"):
        read_symbol_themes(DSN, "005930", 5)
