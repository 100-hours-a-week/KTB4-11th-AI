# KOSPI 200 popularity ranking from relative trading volume

**Date:** 2026-10-22
**Status:** Proposed for review

## Purpose

Issue #367 reports that filtering Kiwoom's whole-market lookup ranking to KOSPI 200 leaves only a few stocks. `/rankings/popular` must instead rank the supported KOSPI 200 universe and return the requested number of stocks when that many have valid market data. Popularity means unusual recent trading volume, measured against each stock's normal volume at the same time of day. Orderbook activity is outside this version.

## Data and ownership

Market-collector already loads the KOSPI 200 codes from `corporation_indices`, collects one-minute bars for those codes, writes them to QuestDB, and replaces `ranking:{popular}` in Valkey once per pass. Keep this ownership and update cadence. Remove the `ka00198` request and its KOSPI 200 filter. The collector reads `bars` after its current pass has written the latest one-minute bars. No new service, dependency, environment variable, table, or Valkey key is needed.

Use only `bars` rows with `timeframe = '1m'`, `session = 'regular'`, and symbols in the loaded KOSPI 200 list. The historical baseline uses the previous five trading sessions that have bars. A stock with incomplete history uses the sessions it has; a stock with no historical bars has no relative-volume score. Do not mix daily bars into minute sums.

## Score and ordering

At each collection pass, let `end` be the latest complete KST minute covered by the pass, capped at the regular close. Sum each stock's one-minute volume in `(end - 30 minutes, end]` for `current_volume`. For each of the previous five available trading sessions, sum the same 30 clock-minute slots, clipped to regular trading hours. `expected_volume` is the arithmetic mean of those session totals. Use available minutes at the start of the trading day for both current and historical windows.

Stocks with positive `current_volume` and positive `expected_volume` get a relative-volume value of `current_volume / expected_volume`. Sort them by descending relative volume, then descending `current_volume`, then code. Stocks without a usable baseline, followed by stocks with zero current volume, fill the remaining places in descending `current_volume` and code order. Publish ordinal sorted-set scores (`eligible_count - rank + 1`), as the current Kiwoom-based ranking does. The ratio determines order but does not change the public meaning of `score`. A fixed share-count threshold is deliberately omitted: the fallback and positive-volume requirement avoid excluding otherwise valid KOSPI 200 stocks solely because they trade fewer shares.

Only codes with a current-session one-minute bar or a historical baseline are eligible. This avoids publishing unsupported or entirely absent symbols. The endpoint can return fewer than the requested limit only when fewer eligible codes exist. It must never be shortened by filtering a whole-market Kiwoom list.

## Publication and HTTP behavior

Replace `ranking:{popular}` and `ranking:{popular}:updated_at` together in the existing Valkey transaction. Keep the current ranking when QuestDB reads or scoring fail; log the failure. Publish no empty replacement when the collector has no eligible codes. Keep the keys hash-tagged for Valkey cluster compatibility. The public `/rankings/popular` response shape, `limit` behavior, metadata lookup, and price enrichment stay as they are. Ranking documentation must describe the new relative-volume ordering.

The rank updates after each collector pass, rather than on each `0B` tick. Historical QuestDB data supplies the comparison; current volume comes from freshly collected minute bars. This keeps ranking calculations out of the high-frequency streamer and avoids rolling Valkey buckets. The collector's existing pass-time warning remains the performance signal.

## Verification

- Unit-test the same-time historical window, trading-day gaps, partial opening window, missing history, zero volume, ties, and the KOSPI 200 eligibility rule.
- Check that a 100-item request can return 100 items when at least 100 eligible symbols have bars, regardless of the Kiwoom whole-market list.
- Check that a failed QuestDB read preserves the previous popular ranking and timestamp.
- Run affected collector and market-http tests, lint, and formatting checks.
- Before deployment, inspect a real trading session's eligible count, score distribution, and collector pass duration. Adjust the scoring policy in a follow-up if thinly traded stocks dominate or the minute pass exceeds its budget.
