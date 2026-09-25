-- infrastructure/questdb/schema/themes.sql
-- Theme groups and their constituents.
--
-- theme_snapshot keys on date_tp as well as theme, because the same theme
-- yields a different dt_prft_rt per period and every collected period is kept.
-- The column keeps its upstream name: its semantics are unconfirmed, and a
-- name like period_return would invite a consumer to reason on a guess.
--
-- theme_members records only memberships inside the KOSPI 200. The collector
-- has no scope outside it, so a row for a symbol with no candles is a row
-- nothing can join against.
--
-- One consequence to keep in mind: theme_snapshot's stock_count, rising_count,
-- falling_count and dt_prft_rt come from Kiwoom, which computes them over a
-- theme's whole membership across the market. They therefore do not match the
-- number of rows stored here, and a consumer must not derive a ratio by
-- combining the two.
--
-- Rows here carry symbols and no fields; the membership is the whole fact.

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
