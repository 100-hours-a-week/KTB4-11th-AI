"""Write QuestDB rows over ILP and read candles over PostgreSQL wire protocol.

QuestDB dedup upserts clear omitted columns, so later writes to the same key
must not omit values already stored.
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
    pass


@dataclass(frozen=True)
class Theme:
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
        """Write memberships whose symbols belong to ``universe``."""
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
    """Read regular-session candles oldest first.

    ``since`` is inclusive. ``limit`` selects the newest rows while preserving
    oldest-first output and must be positive.
    """
    if timeframe not in TIMEFRAME_TABLES:
        raise KeyError(f"unknown timeframe: {timeframe}")
    if limit is not None and limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    import psycopg

    table = TIMEFRAME_TABLES[timeframe]
    since_clause = " AND ts >= %s" if since is not None else ""
    order_clause = "ORDER BY ts DESC LIMIT %s" if limit is not None else "ORDER BY ts ASC"
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
        rows.reverse()
    return rows


def read_themes(dsn: str, date_tp: int, *, limit: int | None = None) -> list[Theme]:
    """Read the latest period snapshot, highest return first.

    Kiwoom's statistics cover all members, while ``members`` contains only the
    stored KOSPI 200 subset. Raises when the requested snapshot is missing.
    """
    if limit is not None and limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    import psycopg

    snapshot_query = cast(
        LiteralString,
        f"SELECT theme_code, theme_name, date_tp, dt_prft_rt, change_rate, stock_count, "
        f"rising_count, falling_count, main_stocks FROM {THEME_SNAPSHOT_TABLE} "
        f"WHERE date_tp = %s AND ts = "
        f"(SELECT max(ts) FROM {THEME_SNAPSHOT_TABLE} WHERE date_tp = %s)",
    )
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
    """Return a symbol's themes, or an empty list when it has none."""
    return [theme for theme in read_themes(dsn, date_tp) if symbol in dict(theme.members)]
