from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from ktb_core.logging import get_logger

__all__ = ["Candle", "CandleRow", "Store"]

TIMEFRAMES = frozenset({"1m", "1d"})
CHECKPOINT_DAYS = 7
CHECKPOINT_QUERIES = {
    timeframe: f"SELECT symbol, '{timeframe}' AS timeframe, ts AS latest_ts FROM bars\n"
    f"WHERE timeframe = '{timeframe}'\nLATEST ON ts PARTITION BY symbol"
    for timeframe in ("1m", "1d")
}
CHECKPOINT_SQL = "\nUNION ALL\n".join(CHECKPOINT_QUERIES.values())
log = get_logger(__name__)


def checkpoint_query(
    symbols: Sequence[str], *, recent: bool = False, timeframe: str | None = None
) -> str:
    if not symbols:
        raise ValueError("checkpoint query needs at least one symbol")
    placeholders = ", ".join(f"${i}" for i in range(1, len(symbols) + 1))
    predicate = f" AND symbol IN ({placeholders})"
    if recent:
        predicate += f" AND ts > dateadd('d', -{CHECKPOINT_DAYS}, now())"
    timeframes = tuple(CHECKPOINT_QUERIES) if timeframe is None else (timeframe,)
    return "\nUNION ALL\n".join(
        CHECKPOINT_QUERIES[tf].replace(
            f"WHERE timeframe = '{tf}'", f"WHERE timeframe = '{tf}'{predicate}"
        )
        for tf in timeframes
    )


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

    def _read_checkpoints(
        self, sql: str, symbols: list[str], phase: str
    ) -> dict[tuple[str, str], datetime]:
        started = perf_counter()
        with self._db.query(sql, symbols) as result:
            records = result.to_pandas().to_dict("records")
        latest = {
            (record["symbol"], record["timeframe"]): _as_utc(record["latest_ts"])
            for record in records
        }
        log.info(
            "checkpoint_query_phase_complete",
            phase=phase,
            elapsed_ms=round((perf_counter() - started) * 1000),
            symbols=len(symbols),
            rows=len(latest),
        )
        return latest

    def latest_bar_timestamps(self, symbols: Iterable[str]) -> dict[tuple[str, str], datetime]:
        started = perf_counter()
        members = sorted(set(symbols))
        latest = {}
        fallback_pairs = 0
        phase = "recent"
        try:
            if members:
                latest = self._read_checkpoints(
                    checkpoint_query(members, recent=True), members, phase
                )
                for timeframe in CHECKPOINT_QUERIES:
                    missing = [symbol for symbol in members if (symbol, timeframe) not in latest]
                    if missing:
                        phase = f"history_{timeframe}"
                        fallback_pairs += len(missing)
                        latest.update(
                            self._read_checkpoints(
                                checkpoint_query(missing, timeframe=timeframe), missing, phase
                            )
                        )
        except Exception:
            log.exception(
                "checkpoint_query_failed",
                phase=phase,
                elapsed_ms=round((perf_counter() - started) * 1000),
            )
            raise
        log.info(
            "checkpoint_query_complete",
            elapsed_ms=round((perf_counter() - started) * 1000),
            rows=len(latest),
            symbols=len(members),
            fallback_pairs=fallback_pairs,
            missing_pairs=len(members) * len(TIMEFRAMES) - len(latest),
        )
        return latest

    def read_regular_candles(self, timeframe: str, symbol: str, limit: int = 300) -> list[Candle]:
        if timeframe not in TIMEFRAMES:
            raise KeyError(f"unknown timeframe: {timeframe}")
        sql = """SELECT ts, high, low, close FROM bars
        WHERE symbol = $1 AND timeframe = $2 AND session = 'regular'
        ORDER BY ts DESC LIMIT $3"""
        with self._db.query(sql, [symbol, timeframe, limit]) as result:
            records = result.to_pandas().to_dict("records")
        return [Candle(**record) for record in reversed(records)]
