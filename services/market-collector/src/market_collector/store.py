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

Each indicator field gets a value column and, beside it, a verdict column
named ``<field>_comment`` — except ``macd_signal``, which ``COMMENT_FIELDS``
excludes because ``ktb_market_analyzer`` has no verdict rule for it. Verdicts
are written into ``symbols`` (QuestDB's dictionary-encoded SYMBOL type), not
``columns``, because the 18-token verdict vocabulary then costs almost
nothing to repeat across millions of rows; the numeric fields go into
``columns`` as DOUBLE.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, LiteralString, Protocol, cast

from market_collector.indicators import COMMENT_FIELDS, INDICATOR_FIELDS
from market_collector.kiwoom.themes import ThemeGroup, ThemeMember

if TYPE_CHECKING:
    from questdb import Sender as QuestDbSender

__all__ = [
    "TIMEFRAME_TABLES",
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
    comments: dict[str, str | None]
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
            for field in COMMENT_FIELDS:
                symbols[f"{field}_comment"] = candle.comments.get(field)

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
        written = 0
        for member in members:
            self._sink.row(
                THEME_MEMBERS_TABLE,
                symbols={
                    "theme_code": member.theme_code,
                    "symbol": member.symbol,
                    "stock_name": member.stock_name,
                },
                columns={"in_universe": member.symbol in universe},
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
    dsn: str, timeframe: str, symbol: str
) -> list[tuple[datetime, float, float, float]]:
    """Regular-session candles for one symbol, oldest first, as (ts, high, low, close).

    The column order is deliberate: it is exactly the three series
    ``ktb_market_analyzer``'s ``Candles`` needs, so a caller builds one with a
    single ``zip`` over the result.
    """
    if timeframe not in TIMEFRAME_TABLES:
        raise KeyError(f"unknown timeframe: {timeframe}")

    import psycopg

    table = TIMEFRAME_TABLES[timeframe]
    # The table name comes from TIMEFRAME_TABLES, not caller input, so the
    # f-string is safe; it is only not a LiteralString because of that
    # interpolation, which is what the cast below tells the type checker.
    query = cast(
        LiteralString,
        f"SELECT ts, high, low, close FROM {table} "
        "WHERE symbol = %s AND session = 'regular' ORDER BY ts ASC",
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(query, (symbol,))
        return [(ts, high, low, close) for ts, high, low, close in cursor.fetchall()]
