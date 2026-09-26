-- infrastructure/questdb/schema/bars.sql
-- Candle tables, one per timeframe. Partition granularity follows candle
-- density: about 408 one-minute candles per trading day per symbol versus one
-- daily candle. DEDUP UPSERT KEYS is load-bearing — the live path (phase 2)
-- rewrites the in-progress candle many times per minute and the post-close
-- reconciliation overwrites what it wrote.
--
-- Indicator values are stored; the verdicts ktb_market_analyzer derives from them
-- are not. A verdict is a pure function of the value it describes -- a threshold
-- pair for the banded fields, a sign and a comparison with the previous bar for
-- the signed ones -- so storing it duplicates nothing and goes stale the moment a
-- threshold changes, silently disagreeing with the value beside it. Callers get
-- verdicts from ktb_market_analyzer at read time, against the rules in force then.
--
-- The indicator columns are nullable, and a null is expected rather than broken:
-- backfilled history carries OHLCV alone unless the collector is explicitly told
-- to compute over it.

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
