"""Write candles and theme data to QuestDB, and read candles back.

Writes go over the InfluxDB line protocol, which is the ingestion path; reads
go over the Postgres wire protocol on port 8812. They are two distinct paths
and the distinction is easy to lose, so they are named apart here.

A column whose value is None is omitted from the row rather than sent. The
line protocol has no null literal, and omitting the column is what leaves it
null in storage — which is exactly what an extended-session candle's indicator
and verdict columns need, and what every column stays for v1's backfilled
history, which carries OHLCV with no indicators or verdicts attached at all.

That omission is not harmless on a row that already exists. Every candle
table is ``DEDUP UPSERT KEYS(ts, symbol)`` (§5 of the design), and confirmed
against the live server: writing ``rsi=55.0`` for a ``(ts, symbol)`` and then
rewriting the same key while omitting ``rsi`` does not leave the stored value
alone — it sets it to null. An omitted column is not "not sent this time", it
is "cleared". So a second write over a row a first write already enriched
with indicators must be a strict superset of that first write's columns,
never a narrower one — see ``backfill.py``'s module docstring for where this
constrains the order backfill and preopen are allowed to run in.

Indicator values are stored; the verdicts ``ktb_market_analyzer`` derives from
them are not. A verdict is a pure function of the value it describes, so storing
it duplicates nothing and goes stale the moment a threshold moves — silently
disagreeing with the value beside it. Callers get verdicts from the analyzer at
read time, against the rules in force then.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, LiteralString, Protocol, cast

from market_collector.indicators import INDICATOR_FIELDS
from market_collector.kiwoom.themes import ThemeGroup, ThemeMember

if TYPE_CHECKING:
    from questdb import Sender as QuestDbSender

__all__ = [
    "TIMEFRAME_TABLES",
    "Candle",
    "CandleRow",
    "EmptyThemeSnapshotError",
    "RowSink",
    "Store",
    "Theme",
    "questdb_sink",
    "read_regular_candles",
    "read_symbol_themes",
    "read_themes",
]

TIMEFRAME_TABLES: dict[str, str] = {
    "1m": "bars_1m",
    "15m": "bars_15m",
    "1h": "bars_1h",
    "1d": "bars_1d",
}

THEME_SNAPSHOT_TABLE = "theme_snapshot"
THEME_MEMBERS_TABLE = "theme_members"


class EmptyThemeSnapshotError(RuntimeError):
    """Raised when no theme snapshot exists to read.

    The same treatment ``universe.EmptyUniverseError`` gets: an empty result
    would read as "no themes moved", which is a claim about the market. It is
    actually a claim about the collector not having run.
    """


@dataclass(frozen=True)
class Theme:
    """One theme's latest snapshot for one period, with its constituents.

    ``members`` holds ``(symbol, stock_name)`` pairs and covers the KOSPI 200
    only, so its length does not match ``stock_count`` -- see that field.
    """

    theme_code: str
    theme_name: str
    date_tp: int
    dt_prft_rt: float | None
    change_rate: float | None
    stock_count: int
    rising_count: int
    falling_count: int
    main_stocks: str
    members: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Candle:
    """A stored candle as read back: prices, and the indicators stored with it.

    ``indicators`` holds every field in ``INDICATOR_FIELDS``, with ``None`` where
    the column is null — the normal state for backfilled history, which stores
    OHLCV alone.

    Prices are here for the indicators that are *not* stored: the analyzer
    computes those on demand from a candle window. The stored values are here so
    a caller that only needs them does not recompute what is already in the table.

    Verdicts are absent because they are not stored. A caller that wants them
    passes the values to ``ktb_market_analyzer``'s ``comment_series``, which
    applies the rules in force at read time rather than whatever they were when
    the row was written.
    """

    ts: datetime
    high: float
    low: float
    close: float
    indicators: dict[str, float | None]


@dataclass(frozen=True)
class CandleRow:
    ts: datetime
    symbol: str
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_value: float | None
    indicators: dict[str, float | None]
    src: str


class RowSink(Protocol):
    def row(
        self,
        table: str,
        *,
        symbols: dict[str, str],
        columns: dict[str, object],
        at: datetime,
    ) -> None: ...

    def flush(self) -> None: ...


def _without_nones(columns: dict[str, object]) -> dict[str, object]:
    return {name: value for name, value in columns.items() if value is not None}


def _without_none_symbols(symbols: dict[str, str | None]) -> dict[str, str]:
    return {name: value for name, value in symbols.items() if value is not None}


class Store:
    def __init__(self, sink: RowSink) -> None:
        self._sink = sink

    def write_candles(self, timeframe: str, rows: Iterable[CandleRow]) -> int:
        if timeframe not in TIMEFRAME_TABLES:
            raise KeyError(f"unknown timeframe: {timeframe}")
        table = TIMEFRAME_TABLES[timeframe]

        written = 0
        for candle in rows:
            columns: dict[str, object] = {
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "trade_value": candle.trade_value,
            }
            for field in INDICATOR_FIELDS:
                columns[field] = candle.indicators.get(field)

            symbols: dict[str, str | None] = {
                "symbol": candle.symbol,
                "session": candle.session,
                "src": candle.src,
            }
            self._sink.row(
                table,
                symbols=_without_none_symbols(symbols),
                columns=_without_nones(columns),
                at=candle.ts,
            )
            written += 1

        self._sink.flush()
        return written

    def write_theme_groups(self, ts: datetime, groups: Iterable[ThemeGroup]) -> int:
        written = 0
        for group in groups:
            self._sink.row(
                THEME_SNAPSHOT_TABLE,
                symbols={"theme_code": group.code, "theme_name": group.name},
                columns=_without_nones(
                    {
                        "date_tp": group.date_tp,
                        "dt_prft_rt": group.dt_prft_rt,
                        "change_rate": group.change_rate,
                        "stock_count": group.stock_count,
                        "rising_count": group.rising_count,
                        "falling_count": group.falling_count,
                        "main_stocks": group.main_stocks,
                    }
                ),
                at=ts,
            )
            written += 1
        self._sink.flush()
        return written

    def write_theme_members(
        self, ts: datetime, members: Iterable[ThemeMember], universe: frozenset[str]
    ) -> int:
        """Write the memberships inside ``universe``, and only those.

        Symbols outside it are dropped rather than stored under a flag. The
        collector's scope is the KOSPI 200 and nothing else, so a row for a
        symbol that has no candles is a row no consumer can join against.

        The rows carry symbols and no fields. QuestDB accepts that — the
        symbols are the series key — and the membership is the entire fact
        there is to record.
        """
        written = 0
        for member in members:
            if member.symbol not in universe:
                continue
            self._sink.row(
                THEME_MEMBERS_TABLE,
                symbols={
                    "theme_code": member.theme_code,
                    "symbol": member.symbol,
                    "stock_name": member.stock_name,
                },
                columns={},
                at=ts,
            )
            written += 1
        self._sink.flush()
        return written


class _QuestDbSink:
    def __init__(self, sender: "QuestDbSender") -> None:
        self._sender = sender

    def row(
        self,
        table: str,
        *,
        symbols: dict[str, str],
        columns: dict[str, object],
        at: datetime,
    ) -> None:
        from questdb import TimestampNanos

        # RowSink's dict types are invariant on the value type and narrower
        # than questdb's own (which also allow None values, numpy arrays,
        # etc.); Any at this one call is the interop boundary with the
        # native client, not a loosening of RowSink's own contract.
        self._sender.row(
            table,
            symbols=cast(Any, symbols),
            columns=cast(Any, columns),
            at=TimestampNanos.from_datetime(at),
        )

    def flush(self) -> None:
        self._sender.flush()


@contextmanager
def questdb_sink(host: str, port: int) -> Iterator[RowSink]:
    from questdb import Protocol as IlpProtocol
    from questdb import Sender

    with Sender(IlpProtocol.Http, host, port) as sender:
        yield _QuestDbSink(sender)


def read_regular_candles(
    dsn: str,
    timeframe: str,
    symbol: str,
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[Candle]:
    """Regular-session candles for one symbol, oldest first.

    Each ``Candle`` carries the prices and the eight indicator values stored with
    that row, so one call serves both readers: the live path seeds its indicator
    window from the prices, and the graph layer reads the stored values without
    recomputing them.

    ``since``, if given, bounds the window from that timestamp onward and is
    *inclusive*: a candle timestamped exactly ``since`` is returned.

    ``limit``, if given, returns the newest ``limit`` candles -- not the
    oldest -- though the result is still returned oldest-first. Combined
    with ``since``, the two together mean "the newest ``limit`` candles at
    or after ``since``". ``limit`` must be a positive integer; zero or a
    negative value raises ``ValueError`` rather than returning an empty
    list, since an empty list would misleadingly read as "no data for this
    symbol" rather than "you asked for zero rows".

    Bound your request generously: ``ktb_market_analyzer``'s indicators need
    warm-up history before they produce a value -- RSI needs 14 prior
    candles, MACD needs 33 -- so asking for only the newest candle, or a
    narrow window, returns candles whose indicators are all NaN.
    """
    if timeframe not in TIMEFRAME_TABLES:
        raise KeyError(f"unknown timeframe: {timeframe}")
    if limit is not None and limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    import psycopg

    table = TIMEFRAME_TABLES[timeframe]
    since_clause = " AND ts >= %s" if since is not None else ""
    order_clause = "ORDER BY ts DESC LIMIT %s" if limit is not None else "ORDER BY ts ASC"
    # The table name comes from TIMEFRAME_TABLES, not caller input;
    # since_clause and order_clause are each picked from a fixed pair of
    # literal strings, never built from since/limit/symbol themselves. So
    # the f-string is safe; it is only not a LiteralString because of that
    # interpolation, which is what the cast below tells the type checker.
    columns = ", ".join(("ts", "high", "low", "close", *INDICATOR_FIELDS))
    query = cast(
        LiteralString,
        f"SELECT {columns} FROM {table} "
        f"WHERE symbol = %s AND session = 'regular'{since_clause} {order_clause}",
    )
    params: tuple[object, ...] = (symbol,)
    if since is not None:
        params += (since,)
    if limit is not None:
        params += (limit,)

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = [
            Candle(
                ts=row[0],
                high=row[1],
                low=row[2],
                close=row[3],
                indicators=dict(zip(INDICATOR_FIELDS, row[4:], strict=True)),
            )
            for row in cursor.fetchall()
        ]

    if limit is not None:
        # We asked the DB for the newest `limit` rows in descending order;
        # reverse them so the function's contract (oldest first) still
        # holds regardless of which bounds a caller combined.
        rows.reverse()
    return rows


def read_themes(dsn: str, date_tp: int, *, limit: int | None = None) -> list[Theme]:
    """The latest theme snapshot for one period, with each theme's constituents.

    One call answers what a reader needs to know about themes: the name, the
    figures Kiwoom reports, and which of our symbols belong to it.

    ``date_tp`` selects the period, because Kiwoom reports different figures
    per period for the same theme -- the same theme measured +299.34 at
    ``date_tp=3`` and +68.45 at ``date_tp=120``, which is why the period is
    part of the dedup key and cannot be defaulted away here.

    Ordered by ``dt_prft_rt`` descending with unknown values last, so the
    themes Kiwoom rates highest come first, and ``limit`` then caps how many
    a caller reads.

    **``stock_count``, ``rising_count``, ``falling_count`` and ``dt_prft_rt``
    are Kiwoom's figures over a theme's whole market-wide membership**, while
    ``members`` holds only the KOSPI 200 constituents this service stores. The
    two do not match and must never be combined into a ratio -- "3 of our 5
    members are rising" is not a statement these numbers support.

    Raises ``EmptyThemeSnapshotError`` when nothing has been collected for
    ``date_tp``, naming the subcommand that fixes it.
    """
    if limit is not None and limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    import psycopg

    # Unlike read_regular_candles, the ordering and the limit are applied here
    # rather than in SQL. A candle table holds thousands of rows per symbol, so
    # bounding the query matters; a snapshot holds one row per theme per period
    # -- 142 on the measured day -- and QuestDB has no NULLS LAST, which the
    # dt_prft_rt ordering needs because that column is nullable.
    snapshot_query = cast(
        LiteralString,
        f"SELECT theme_code, theme_name, date_tp, dt_prft_rt, change_rate, stock_count, "
        f"rising_count, falling_count, main_stocks FROM {THEME_SNAPSHOT_TABLE} "
        f"WHERE date_tp = %s AND ts = "
        f"(SELECT max(ts) FROM {THEME_SNAPSHOT_TABLE} WHERE date_tp = %s)",
    )
    # theme_members carries no date_tp: memberships do not vary by period, so
    # snapshot() collects them once per run against one reference period.
    members_query = cast(
        LiteralString,
        f"SELECT theme_code, symbol, stock_name FROM {THEME_MEMBERS_TABLE} "
        f"WHERE ts = (SELECT max(ts) FROM {THEME_MEMBERS_TABLE}) ORDER BY symbol",
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(snapshot_query, (date_tp, date_tp))
        snapshots = cursor.fetchall()
        cursor.execute(members_query)
        member_rows = cursor.fetchall()

    if not snapshots:
        raise EmptyThemeSnapshotError(
            f"no theme_snapshot rows for date_tp={date_tp}; run `market-collector themes` first"
        )

    by_theme: dict[str, list[tuple[str, str]]] = {}
    for theme_code, symbol, stock_name in member_rows:
        by_theme.setdefault(theme_code, []).append((symbol, stock_name))

    themes = [
        Theme(
            theme_code=row[0],
            theme_name=row[1],
            date_tp=row[2],
            dt_prft_rt=row[3],
            change_rate=row[4],
            stock_count=row[5],
            rising_count=row[6],
            falling_count=row[7],
            main_stocks=row[8],
            members=tuple(by_theme.get(row[0], ())),
        )
        for row in snapshots
    ]
    themes.sort(key=lambda t: (t.dt_prft_rt is None, -(t.dt_prft_rt or 0.0), t.theme_code))
    return themes if limit is None else themes[:limit]


def read_symbol_themes(dsn: str, symbol: str, date_tp: int) -> list[Theme]:
    """The themes one symbol belongs to, highest-rated first.

    The reverse of ``read_themes``, and the question a reader asks about a
    single stock: which themes is it in, how are those themes doing, and which
    other symbols move with it. Each returned ``Theme`` keeps its full member
    list for that last part -- the peers are usually the point.

    A symbol in no theme returns an empty list, which is an ordinary answer:
    plenty of constituents belong to none. Only a missing snapshot raises, and
    that comes from ``read_themes``.

    This filters the period's snapshot in Python rather than querying
    ``theme_members`` by its ``symbol`` index. The answer needs every matching
    theme's figures *and* its other members, so a narrower query would still
    have to fetch both tables afterwards; at one row per theme per period the
    whole snapshot costs less than the extra round trips.
    """
    return [theme for theme in read_themes(dsn, date_tp) if symbol in dict(theme.members)]
