from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from market_collector.kiwoom.themes import ThemeGroup, ThemeMember

__all__ = ["Candle", "CandleRow", "Store"]

TIMEFRAMES = frozenset({"1m", "15m", "1h", "1d"})


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
    src: str


def _without_nones(columns: dict[str, object]) -> dict[str, object]:
    return {name: value for name, value in columns.items() if value is not None}


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
                            "trade_value": candle.trade_value,
                        }
                    ),
                    at=candle.ts,
                )
                written += 1
            sender.flush()
        return written

    def write_theme_groups(self, ts: datetime, groups: Iterable[ThemeGroup]) -> int:
        written = 0
        with self._db.sender() as sender:
            for group in groups:
                sender.row(
                    "theme_snapshot",
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
            sender.flush()
        return written

    def write_theme_members(
        self, ts: datetime, members: Iterable[ThemeMember], universe: frozenset[str]
    ) -> int:
        written = 0
        with self._db.sender() as sender:
            for member in members:
                if member.symbol not in universe:
                    continue
                sender.row(
                    "theme_members",
                    symbols={
                        "theme_code": member.theme_code,
                        "symbol": member.symbol,
                        "stock_name": member.stock_name,
                    },
                    columns={},
                    at=ts,
                )
                written += 1
            sender.flush()
        return written

    def write_universe_members(
        self,
        ts: datetime,
        index_code: str,
        index_name: str,
        src: str,
        members: Iterable[tuple[str, str]],
    ) -> int:
        written = 0
        with self._db.sender() as sender:
            for symbol, stock_name in members:
                sender.row(
                    "universe_members",
                    symbols={
                        "index_code": index_code,
                        "index_name": index_name,
                        "symbol": symbol,
                        "stock_name": stock_name,
                        "src": src,
                    },
                    columns={},
                    at=ts,
                )
                written += 1
            sender.flush()
        return written

    def latest_members(self, index_code: str) -> frozenset[str]:
        sql = """SELECT symbol FROM universe_members
        WHERE index_code = $1
          AND ts = (SELECT max(ts) FROM universe_members WHERE index_code = $1)"""
        with self._db.query(sql, [index_code]) as result:
            return frozenset(result.to_pandas()["symbol"].tolist())

    def read_regular_candles(self, timeframe: str, symbol: str, limit: int = 300) -> list[Candle]:
        if timeframe not in TIMEFRAMES:
            raise KeyError(f"unknown timeframe: {timeframe}")
        sql = """SELECT ts, high, low, close FROM bars
        WHERE symbol = $1 AND timeframe = $2 AND session = 'regular'
        ORDER BY ts DESC LIMIT $3"""
        with self._db.query(sql, [symbol, timeframe, limit]) as result:
            records = result.to_pandas().to_dict("records")
        return [Candle(**record) for record in reversed(records)]
