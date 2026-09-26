CREATE TABLE IF NOT EXISTS theme_snapshot (
    ts TIMESTAMP,
    theme_code SYMBOL INDEX,
    theme_name SYMBOL,
    date_tp INT,
    dt_prft_rt DOUBLE,
    change_rate DOUBLE,
    stock_count INT,
    rising_count INT,
    falling_count INT,
    main_stocks STRING
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, theme_code, date_tp);

CREATE TABLE IF NOT EXISTS theme_members (
    ts TIMESTAMP,
    theme_code SYMBOL INDEX,
    symbol SYMBOL INDEX,
    stock_name SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, theme_code, symbol);
