import questdb


def last_closes(conf: str, stock_codes: set[str]) -> dict[str, float]:
    if not stock_codes:
        return {}
    codes = sorted(stock_codes)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(codes)))
    sql = (
        "SELECT symbol, close FROM bars"
        f" WHERE timeframe = '1d' AND session = 'regular' AND symbol IN ({placeholders})"
        " LATEST ON ts PARTITION BY symbol"
    )
    with questdb.connect(conf) as db, db.query(sql, codes) as result:
        return {r["symbol"]: float(r["close"]) for r in result.to_pandas().to_dict("records")}
