from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

__all__ = ["Candle", "CandleRow", "Store"]

TIMEFRAMES = frozenset({"1m", "1d"})


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
    src: str


def _without_nones(columns: dict[str, object]) -> dict[str, object]:
    return {name: value for name, value in columns.items() if value is not None}


def _as_utc(timestamp: datetime) -> datetime:
    return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)


class Store:
    def __init__(self, db: Any) -> None:
        self._db = db

    def write_candles(self, timeframe: str, rows: Iterable[CandleRow]) -> int:
        if timeframe not in TIMEFRAMES:
            raise KeyError(f"unknown timeframe: {timeframe}")

        written = 0
        with self._db.sender() as sender:
            for candle in rows:
                sender.row(
                    "bars",
                    symbols={
                        "symbol": candle.symbol,
                        "session": candle.session,
                        "src": candle.src,
                    },
                    columns=_without_nones(
                        {
                            "timeframe": timeframe,
                            "open": candle.open,
                            "high": candle.high,
                            "low": candle.low,
                            "close": candle.close,
                            "volume": candle.volume,
                        }
                    ),
                    at=candle.ts,
                )
                written += 1
            sender.flush()
        return written

    def latest_bar_timestamps(self) -> dict[tuple[str, str], datetime]:
        sql = """SELECT symbol, timeframe, max(ts) AS latest_ts FROM bars
        WHERE timeframe IN ('1m', '1d')
        GROUP BY symbol, timeframe"""
        with self._db.query(sql) as result:
            records = result.to_pandas().to_dict("records")
        return {
            (record["symbol"], record["timeframe"]): _as_utc(record["latest_ts"])
            for record in records
        }

    def read_regular_candles(self, timeframe: str, symbol: str, limit: int = 300) -> list[Candle]:
        if timeframe not in TIMEFRAMES:
            raise KeyError(f"unknown timeframe: {timeframe}")
        sql = """SELECT ts, high, low, close FROM bars
        WHERE symbol = $1 AND timeframe = $2 AND session = 'regular'
        ORDER BY ts DESC LIMIT $3"""
        with self._db.query(sql, [symbol, timeframe, limit]) as result:
            records = result.to_pandas().to_dict("records")
        return [Candle(**record) for record in reversed(records)]
