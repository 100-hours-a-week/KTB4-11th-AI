from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


type SessionCache = dict[tuple[date, tuple[str, ...]], list[date]]


def popular_codes(
    db: Any, symbols: list[str], now: datetime, session_cache: SessionCache | None = None
) -> list[str]:
    now = now.astimezone(KST)
    opening = now.replace(hour=9, minute=0, second=0, microsecond=0)
    end = min(
        now.replace(second=0, microsecond=0) - timedelta(minutes=1),
        opening.replace(hour=15, minute=30),
    )
    if not symbols or end < opening:
        return []
    symbols = sorted(set(symbols))
    placeholders = ", ".join(f"${i}" for i in range(1, len(symbols) + 1))
    predicate = f"timeframe = '1m' AND session = 'regular' AND symbol IN ({placeholders})"
    midnight = opening.replace(hour=0)
    cache_key = (now.date(), tuple(symbols))
    days = session_cache.get(cache_key) if session_cache is not None else None
    if days is None:
        days = []
        for lookback in (14, 28, 56, 112, None):
            binds = list(symbols)
            lower_predicate = ""
            if lookback is not None:
                lower = midnight - timedelta(days=lookback)
                binds.append(lower.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
                lower_predicate = f" AND timestamp >= cast(${len(binds)} AS TIMESTAMP)"
            binds.append(midnight.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
            with db.query(
                "SELECT DISTINCT date_trunc('day', dateadd('h', 9, timestamp)) AS session_day"
                f" FROM bars WHERE {predicate}{lower_predicate}"
                f" AND timestamp < cast(${len(binds)} AS TIMESTAMP)"
                " ORDER BY session_day DESC LIMIT 5",
                binds,
            ) as result:
                days = [row["session_day"].date() for row in result.to_pandas().to_dict("records")]
            if len(days) == 5:
                break
        if session_cache is not None:
            session_cache.clear()
            session_cache[cache_key] = days
    binds = [
        *symbols,
        opening.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    ]
    with db.query(
        f"SELECT DISTINCT symbol FROM bars WHERE {predicate}"
        f" AND timestamp >= cast(${len(binds) - 1} AS TIMESTAMP)"
        f" AND timestamp <= cast(${len(binds)} AS TIMESTAMP)",
        binds,
    ) as result:
        current = {row["symbol"] for row in result.to_pandas().to_dict("records")}
    if not current:
        return []
    start = max(end - timedelta(minutes=30), opening - timedelta(minutes=1))
    binds = list(symbols)
    windows = []
    for day in [now.date(), *days]:
        for boundary in (start, end):
            binds.append(
                datetime.combine(day, boundary.timetz())
                .astimezone(UTC)
                .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            )
        windows.append(
            f"(timestamp > cast(${len(binds) - 1} AS TIMESTAMP)"
            f" AND timestamp <= cast(${len(binds)} AS TIMESTAMP))"
        )
    with db.query(
        "SELECT symbol, date_trunc('day', dateadd('h', 9, timestamp)) AS session_day,"
        f" sum(volume) AS volume FROM bars WHERE {predicate} AND ({' OR '.join(windows)})"
        " GROUP BY symbol, session_day",
        binds,
    ) as result:
        rows = result.to_pandas().to_dict("records")
    volumes: dict[str, int] = defaultdict(int)
    history: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if row["session_day"].date() == now.date():
            volumes[row["symbol"]] = row["volume"]
        else:
            history[row["symbol"]].append(row["volume"])
    ranked = []
    for symbol in current | history.keys():
        volume = volumes[symbol]
        baseline = sum(history[symbol])
        if volume > 0 and baseline > 0:
            ranked.append((0, -Fraction(volume * len(history[symbol]), baseline), -volume, symbol))
        else:
            ranked.append((1 if volume > 0 else 2, Fraction(0), -volume, symbol))
    return [symbol for _, _, _, symbol in sorted(ranked)]
