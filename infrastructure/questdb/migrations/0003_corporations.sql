CREATE TABLE IF NOT EXISTS corporations (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    trade_date VARCHAR,
    fetched_at TIMESTAMP,
    base_price LONG,
    upper_limit_price LONG,
    lower_limit_price LONG,
    eps LONG,
    bps LONG
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, symbol);
