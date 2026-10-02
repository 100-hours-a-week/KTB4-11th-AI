from datetime import UTC, datetime

import questdb

from portfolio_rebalancer.holidays import KST

# ponytail: 60 calendar days covers 20 sessions through the longest KRX holiday run;
# widen it if a symbol ever comes back short.
DAILY = (
    "SELECT symbol, ts, close FROM bars"
    " WHERE timeframe = '1d' AND session = 'regular' AND symbol IN ({codes})"
    " AND ts > dateadd('d', -60, now()) ORDER BY ts"
)
LATEST = (
    "SELECT symbol, close FROM bars"
    " WHERE timeframe = '1m' AND session = 'regular' AND symbol IN ({codes})"
    " LATEST ON ts PARTITION BY symbol"
)


def _query(conf: str, sql: str, stock_codes: set[str]) -> list[dict]:
    if not stock_codes:
        return []
    codes = sorted(stock_codes)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(codes)))
    with questdb.connect(conf) as db, db.query(sql.format(codes=placeholders), codes) as result:
        return result.to_pandas().to_dict("records")


def recent_closes(records: list[dict], before: datetime, n: int) -> dict[str, list[float]]:
    closes: dict[str, list[float]] = {}
    for record in records:
        ts = record["ts"]
        if (ts if ts.tzinfo else ts.replace(tzinfo=UTC)) < before:
            closes.setdefault(record["symbol"], []).append(float(record["close"]))
    return {symbol: c[-n:] for symbol, c in closes.items() if len(c) >= n}


def daily_closes(
    conf: str, stock_codes: set[str], now: datetime, n: int = 20
) -> dict[str, list[float]]:
    midnight = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return recent_closes(_query(conf, DAILY, stock_codes), midnight, n)


def latest_prices(conf: str, stock_codes: set[str]) -> dict[str, float]:
    return {r["symbol"]: float(r["close"]) for r in _query(conf, LATEST, stock_codes)}
