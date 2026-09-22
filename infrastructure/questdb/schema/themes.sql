-- infrastructure/questdb/schema/themes.sql
-- Theme groups and their constituents.
--
-- theme_snapshot keys on date_tp as well as theme, because the same theme
-- yields a different dt_prft_rt per period and every collected period is kept.
-- The column keeps its upstream name: its semantics are unconfirmed, and a
-- name like period_return would invite a consumer to reason on a guess.
--
-- theme_members records every membership, including symbols outside the
-- KOSPI 200, so that stock_count and dt_prft_rt stay interpretable. Kiwoom
-- computes them over all members. in_universe says whether candles exist for
-- the symbol, which lets a consumer tell "no data" from "no signal".

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
    stock_name SYMBOL,
    in_universe BOOLEAN
) TIMESTAMP(ts) PARTITION BY MONTH WAL DEDUP UPSERT KEYS(ts, theme_code, symbol);
