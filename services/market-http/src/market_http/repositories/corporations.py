from datetime import UTC, datetime, time

import questdb
from ktb_core.krx import KST

from market_http.repositories.questdb import questdb_time, records


def current_day_price_limits(conf: str, codes: list[str]) -> dict[str, dict[str, int]]:
    placeholders = ", ".join(f"${index}" for index in range(2, len(codes) + 2))
    sql = (
        "SELECT symbol, upper_limit_price, lower_limit_price FROM corporations "
        f"WHERE timestamp = cast($1 AS TIMESTAMP) AND symbol IN ({placeholders})"
        " AND upper_limit_price IS NOT NULL AND lower_limit_price IS NOT NULL"
    )
    day = datetime.now(KST).date()
    with questdb.connect(conf) as db:
        rows = records(db, sql, [questdb_time(datetime.combine(day, time(), UTC)), *codes])
    return {
        row["symbol"]: {
            "upper_limit_price": int(row["upper_limit_price"]),
            "lower_limit_price": int(row["lower_limit_price"]),
        }
        for row in rows
    }
