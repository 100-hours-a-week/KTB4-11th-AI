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
    "RowSink",
    "Store",
    "questdb_sink",
    "read_regular_candles",
]

TIMEFRAME_TABLES: dict[str, str] = {
    "1m": "bars_1m",
    "15m": "bars_15m",
    "1h": "bars_1h",
    "1d": "bars_1d",
}

THEME_SNAPSHOT_TABLE = "theme_snapshot"
THEME_MEMBERS_TABLE = "theme_members"


@dataclass(frozen=True)
class Candle:
    ts: datetime
    high: float
    low: float
    close: float


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

    query = cast(
        LiteralString,
        f"SELECT ts, high, low, close FROM {table} "
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
            Candle(ts=ts, high=high, low=low, close=close)
            for ts, high, low, close in cursor.fetchall()
        ]

    if limit is not None:
        rows.reverse()
    return rows
