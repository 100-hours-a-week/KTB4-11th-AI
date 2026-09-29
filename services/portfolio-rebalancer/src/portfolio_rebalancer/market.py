from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import questdb

TIMEFRAMES = frozenset({"1m", "1d"})


def connect(questdb_conf: str) -> Any:
    return questdb.connect(questdb_conf)


@dataclass(frozen=True)
class Price:
    close: float
    ts: datetime


def latest_prices(
    db: Any,
    stock_codes: Iterable[str],
    timeframe: str = "1m",
) -> dict[str, Price]:
    # A code with no candle is left out rather than priced at zero, which would read as a
    # free share. One statement covers every code: QuestDB's LATEST ON applies the session
    # and timeframe filters before picking the newest row per symbol.
    if timeframe not in TIMEFRAMES:
        raise KeyError(f"unknown timeframe: {timeframe}")

    codes = list(dict.fromkeys(stock_codes))
    if not codes:
        return {}

    placeholders = ", ".join(f"${index + 2}" for index in range(len(codes)))
    sql = f"""SELECT symbol, close, ts FROM bars
    WHERE timeframe = $1 AND session = 'regular' AND symbol IN ({placeholders})
    LATEST ON ts PARTITION BY symbol"""
    with db.query(sql, [timeframe, *codes]) as result:
        records = result.to_pandas().to_dict("records")

    return {
        record["symbol"]: Price(close=float(record["close"]), ts=_as_utc(record["ts"]))
        for record in records
    }


def _as_utc(timestamp: Any) -> datetime:
    # QuestDB hands back a naive timestamp that is already UTC.
    moment = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
