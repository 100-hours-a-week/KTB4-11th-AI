import questdb

from market_http.repositories.questdb import records


def latest_price_limits(conf: str, codes: list[str]) -> dict[str, dict[str, int]]:
    placeholders = ", ".join(f"${index}" for index in range(1, len(codes) + 1))
    sql = (
        "SELECT symbol, upper_limit_price, lower_limit_price FROM corporations "
        f"WHERE symbol IN ({placeholders})"
        " AND upper_limit_price IS NOT NULL AND lower_limit_price IS NOT NULL"
        " LATEST ON timestamp PARTITION BY symbol"
    )
    with questdb.connect(conf) as db:
        rows = records(db, sql, codes)
    return {
        row["symbol"]: {
            "upper_limit_price": int(row["upper_limit_price"]),
            "lower_limit_price": int(row["lower_limit_price"]),
        }
        for row in rows
    }
