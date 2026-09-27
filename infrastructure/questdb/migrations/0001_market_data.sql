CREATE TABLE IF NOT EXISTS bars (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    timeframe VARCHAR,
    session SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG,
    trade_value DOUBLE,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY DAY WAL DEDUP UPSERT KEYS(ts, symbol, timeframe);

CREATE VIEW IF NOT EXISTS bars_1m AS (SELECT * FROM bars WHERE timeframe = '1m');
CREATE VIEW IF NOT EXISTS bars_15m AS (SELECT * FROM bars WHERE timeframe = '15m');
CREATE VIEW IF NOT EXISTS bars_1h AS (SELECT * FROM bars WHERE timeframe = '1h');
CREATE VIEW IF NOT EXISTS bars_1d AS (SELECT * FROM bars WHERE timeframe = '1d');

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
    main_stocks VARCHAR
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, theme_code, date_tp);

CREATE TABLE IF NOT EXISTS theme_members (
    ts TIMESTAMP,
    theme_code SYMBOL INDEX,
    symbol SYMBOL INDEX,
    stock_name SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, theme_code, symbol);

CREATE TABLE IF NOT EXISTS universe_members (
    ts TIMESTAMP,
    index_code SYMBOL INDEX,
    index_name SYMBOL,
    symbol SYMBOL INDEX,
    stock_name SYMBOL,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, index_code, symbol);
