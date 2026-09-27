import re
from datetime import datetime
from typing import LiteralString, cast

import numpy as np
import psycopg
from ktb_market_analyzer import Candles, interpret
from ktb_market_analyzer.descriptions import DESCRIPTIONS

TIMEFRAME_TABLES: dict[str, str] = {
    "1m": "bars_1m",
    "15m": "bars_15m",
    "1h": "bars_1h",
    "1d": "bars_1d",
}

# Stored codes are six alphanumeric characters (0126Z0 is real). Callers also write
# Kiwoom's A-prefixed form or a Yahoo-style .KS/.KQ suffix, so both are dropped.
_SYMBOL = re.compile(r"A?([0-9A-Z]{6})(?:\.K[SQ])?")


def normalize_symbol(raw: str) -> str | None:
    match = _SYMBOL.fullmatch(raw.strip().upper())
    return match.group(1) if match else None


def read_candles(
    dsn: str, timeframe: str, symbol: str, limit: int
) -> tuple[Candles, datetime] | None:
    """The newest ``limit`` regular-session candles, oldest first, and the newest ``ts``."""
    query = cast(
        LiteralString,
        f"SELECT ts, high, low, close FROM {TIMEFRAME_TABLES[timeframe]} "
        "WHERE symbol = %s AND session = 'regular' ORDER BY ts DESC LIMIT %s",
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(query, (symbol, limit))
        rows = cursor.fetchall()
    if not rows:
        return None

    rows.reverse()
    _, high, low, close = (np.array(column) for column in zip(*rows, strict=True))
    candles = Candles(
        high=high.astype(np.float64), low=low.astype(np.float64), close=close.astype(np.float64)
    )
    return candles, rows[-1][0]


def describe(symbol: str, timeframe: str, candles: Candles, newest: datetime) -> str:
    lines = [
        f"{symbol} {timeframe}: {candles.close.size} regular-session candles, "
        f"newest {newest.isoformat()} (may still be in progress), close {candles.close[-1]:g}",
    ]
    for field in DESCRIPTIONS:
        reading = interpret(field, candles)
        if reading.value is None:
            verdict = "insufficient data"
        elif reading.comment is None:
            verdict = f"{reading.value:.2f}"
        else:
            verdict = f"{reading.value:.2f} [{reading.comment}] {reading.comment_reasoning}"
        lines.append(f"- {field}: {verdict}\n  {reading.description}")
    return "\n".join(lines)
