"""Read candles out of QuestDB. The only module here that touches a database."""

from datetime import datetime
from typing import LiteralString, cast

import numpy as np
from ktb_market_analyzer import Candles

__all__ = ["TIMEFRAMES", "read_candles"]

# bars_1m and bars_1d are views over `bars` and carry every column, session
# included. bars_15m and bars_1h are materialized views that SAMPLE BY over the
# 1m rows and select only ts, symbol and OHLCV -- asking them for `session`
# fails with "Invalid column: session", so only the first two can be filtered.
TIMEFRAMES: dict[str, bool] = {
    "1m": True,
    "15m": False,
    "1h": False,
    "1d": True,
}


def read_candles(
    dsn: str, timeframe: str, symbol: str, limit: int
) -> tuple[Candles, datetime] | None:
    """The newest ``limit`` candles for one symbol, oldest first, and the newest ``ts``.

    Returns ``None`` when the symbol has no candles at that timeframe.
    """
    session_clause = " AND session = 'regular'" if TIMEFRAMES[timeframe] else ""
    query = cast(
        LiteralString,
        f"SELECT ts, high, low, close FROM bars_{timeframe} "
        f"WHERE symbol = %s{session_clause} ORDER BY ts DESC LIMIT %s",
    )
    import psycopg

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(query, (symbol, limit))
        rows = cursor.fetchall()
    if not rows:
        return None

    rows.reverse()
    _, *prices = zip(*rows, strict=True)
    high, low, close = (np.array(column, dtype=np.float64) for column in prices)
    return Candles(high=high, low=low, close=close), rows[-1][0]
