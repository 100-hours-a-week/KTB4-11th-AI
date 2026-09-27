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
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY DAY WAL DEDUP UPSERT KEYS(ts, symbol, timeframe);

CREATE VIEW IF NOT EXISTS bars_1m AS (SELECT * FROM bars WHERE timeframe = '1m');
CREATE VIEW IF NOT EXISTS bars_1d AS (SELECT * FROM bars WHERE timeframe = '1d');

CREATE MATERIALIZED VIEW IF NOT EXISTS bars_15m AS (
    SELECT
        ts,
        symbol,
        first(open) AS open,
        max(high) AS high,
        min(low) AS low,
        last(close) AS close,
        sum(volume) AS volume
    FROM bars
    WHERE timeframe = '1m'
    SAMPLE BY 15m
) PARTITION BY MONTH;

CREATE MATERIALIZED VIEW IF NOT EXISTS bars_1h AS (
    SELECT
        ts,
        symbol,
        first(open) AS open,
        max(high) AS high,
        min(low) AS low,
        last(close) AS close,
        sum(volume) AS volume
    FROM bars
    WHERE timeframe = '1m'
    SAMPLE BY 1h
) PARTITION BY MONTH;

CREATE TABLE IF NOT EXISTS universe_members (
    ts TIMESTAMP,
    index_code SYMBOL INDEX,
    index_name SYMBOL,
    symbol SYMBOL INDEX,
    stock_name SYMBOL,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, index_code, symbol);
