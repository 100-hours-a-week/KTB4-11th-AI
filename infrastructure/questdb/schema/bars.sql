-- infrastructure/questdb/schema/bars.sql
-- Candle tables, one per timeframe. Partition granularity follows candle
-- density: about 408 one-minute candles per trading day per symbol versus one
-- daily candle. DEDUP UPSERT KEYS is load-bearing — the live path (phase 2)
-- rewrites the in-progress candle many times per minute and the post-close
-- reconciliation overwrites what it wrote.

CREATE TABLE IF NOT EXISTS bars_1m (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    session SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG,
    trade_value DOUBLE,
    rsi DOUBLE,
    macd DOUBLE,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    stochastic_k DOUBLE,
    stochastic_d DOUBLE,
    roc DOUBLE,
    williams_r DOUBLE,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY DAY WAL DEDUP UPSERT KEYS(ts, symbol);

CREATE TABLE IF NOT EXISTS bars_15m (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    session SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG,
    trade_value DOUBLE,
    rsi DOUBLE,
    macd DOUBLE,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    stochastic_k DOUBLE,
    stochastic_d DOUBLE,
    roc DOUBLE,
    williams_r DOUBLE,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, symbol);

CREATE TABLE IF NOT EXISTS bars_1h (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    session SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG,
    trade_value DOUBLE,
    rsi DOUBLE,
    macd DOUBLE,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    stochastic_k DOUBLE,
    stochastic_d DOUBLE,
    roc DOUBLE,
    williams_r DOUBLE,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, symbol);

CREATE TABLE IF NOT EXISTS bars_1d (
    ts TIMESTAMP,
    symbol SYMBOL INDEX,
    session SYMBOL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    volume LONG,
    trade_value DOUBLE,
    rsi DOUBLE,
    macd DOUBLE,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    stochastic_k DOUBLE,
    stochastic_d DOUBLE,
    roc DOUBLE,
    williams_r DOUBLE,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY YEAR WAL DEDUP UPSERT KEYS(ts, symbol);
