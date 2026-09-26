CREATE TABLE IF NOT EXISTS universe_members (
    ts TIMESTAMP,
    index_code SYMBOL INDEX,
    index_name SYMBOL,
    symbol SYMBOL INDEX,
    stock_name SYMBOL,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, index_code, symbol);
