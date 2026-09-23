-- infrastructure/questdb/schema/bars.sql
-- Candle tables, one per timeframe. Partition granularity follows candle
-- density: about 408 one-minute candles per trading day per symbol versus one
-- daily candle. DEDUP UPSERT KEYS is load-bearing — the live path (phase 2)
-- rewrites the in-progress candle many times per minute and the post-close
-- reconciliation overwrites what it wrote.
--
-- Each indicator carries its value and, beside it, the verdict ktb_market_analyzer
-- computed for that value. The pairing is written adjacently on purpose: the one
-- field with no verdict, macd_signal, is then visible as a gap rather than as an
-- omission someone has to look up. Verdicts are a closed vocabulary of 18 tokens,
-- so SYMBOL's dictionary encoding stores them for almost nothing.
--
-- Both columns are nullable, and a value with a null verdict is expected rather
-- than broken: indicators are only attached to candles collected after the
-- service starts running. Backfilled history carries OHLCV alone unless the
-- collector is explicitly told to compute over it.

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
    rsi_comment SYMBOL,
    macd DOUBLE,
    macd_comment SYMBOL,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    macd_histogram_comment SYMBOL,
    stochastic_k DOUBLE,
    stochastic_k_comment SYMBOL,
    stochastic_d DOUBLE,
    stochastic_d_comment SYMBOL,
    roc DOUBLE,
    roc_comment SYMBOL,
    williams_r DOUBLE,
    williams_r_comment SYMBOL,
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
    rsi_comment SYMBOL,
    macd DOUBLE,
    macd_comment SYMBOL,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    macd_histogram_comment SYMBOL,
    stochastic_k DOUBLE,
    stochastic_k_comment SYMBOL,
    stochastic_d DOUBLE,
    stochastic_d_comment SYMBOL,
    roc DOUBLE,
    roc_comment SYMBOL,
    williams_r DOUBLE,
    williams_r_comment SYMBOL,
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
    rsi_comment SYMBOL,
    macd DOUBLE,
    macd_comment SYMBOL,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    macd_histogram_comment SYMBOL,
    stochastic_k DOUBLE,
    stochastic_k_comment SYMBOL,
    stochastic_d DOUBLE,
    stochastic_d_comment SYMBOL,
    roc DOUBLE,
    roc_comment SYMBOL,
    williams_r DOUBLE,
    williams_r_comment SYMBOL,
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
    rsi_comment SYMBOL,
    macd DOUBLE,
    macd_comment SYMBOL,
    macd_signal DOUBLE,
    macd_histogram DOUBLE,
    macd_histogram_comment SYMBOL,
    stochastic_k DOUBLE,
    stochastic_k_comment SYMBOL,
    stochastic_d DOUBLE,
    stochastic_d_comment SYMBOL,
    roc DOUBLE,
    roc_comment SYMBOL,
    williams_r DOUBLE,
    williams_r_comment SYMBOL,
    src SYMBOL
) TIMESTAMP(ts) PARTITION BY YEAR WAL DEDUP UPSERT KEYS(ts, symbol);
