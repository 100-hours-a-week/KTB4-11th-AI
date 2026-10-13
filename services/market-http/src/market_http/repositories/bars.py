from datetime import datetime
from typing import Literal

import questdb
from pydantic import BaseModel

from market_http.repositories.questdb import as_utc, questdb_time, records

Timeframe = Literal["1m", "15m", "1h", "1d"]

VIEWS = {"1m": "bars_1m", "15m": "bars_15m", "1h": "bars_1h", "1d": "bars_1d"}
SESSION_FILTERED = {"1m", "1d"}


class Bar(BaseModel):
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


def find_bars(
    conf: str, code: str, timeframe: Timeframe, limit: int, before: datetime | None
) -> list[Bar]:
    sql = f"SELECT ts, open, high, low, close, volume FROM {VIEWS[timeframe]} WHERE symbol = $1"
    binds: list[object] = [code]
    if timeframe in SESSION_FILTERED:
        sql += " AND session = 'regular'"
    if before is not None:
        binds.append(questdb_time(before))
        sql += f" AND ts < cast(${len(binds)} AS TIMESTAMP)"
    binds.append(limit)
    sql += f" ORDER BY ts DESC LIMIT ${len(binds)}"
    with questdb.connect(conf) as db:
        rows = records(db, sql, binds)
    return [
        Bar(
            ts=as_utc(row["ts"]),
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=int(row["volume"]),
        )
        for row in rows
    ]
