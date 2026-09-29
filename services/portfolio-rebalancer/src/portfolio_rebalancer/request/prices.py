"""Read the price a share would be bought at, from the candles the collector wrote.

This fills the gap the Backend poll leaves: the poll quotes what the account holds, so a
stock being bought for the first time has no price in it. QuestDB's last close is the only
number available for those, and it is what the first order's reference is set from.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import questdb

__all__ = ["Price", "connect", "latest_prices"]

TIMEFRAMES = frozenset({"1m", "1d"})


def connect(questdb_conf: str) -> Any:
    """Open the QuestDB handle this module reads through, as backend.py does for HTTP."""
    return questdb.connect(questdb_conf)


@dataclass(frozen=True)
class Price:
    """A close and when it was struck, so a caller can refuse one that has gone stale."""

    close: float
    ts: datetime


def latest_prices(
    db: Any,
    stock_codes: Iterable[str],
    timeframe: str = "1m",
) -> dict[str, Price]:
    """The latest regular-session close for each code, keyed by code.

    A code with no candle is left out rather than priced at zero, which would read as a
    free share. One statement covers every code: QuestDB's LATEST ON applies the session
    and timeframe filters before picking the newest row per symbol.
    """
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
    """QuestDB hands back a naive timestamp that is already UTC."""
    moment = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
