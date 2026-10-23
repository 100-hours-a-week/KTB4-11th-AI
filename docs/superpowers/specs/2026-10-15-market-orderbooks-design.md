# KOSPI 200 live orderbooks

**Date:** 2026-10-15
**Status:** Approved for planning

## Purpose

The Backend and Frontend need the current ten ask levels and ten bid levels, including the quantity at each level, for KOSPI 200 stocks. `market-http` will expose the latest orderbook through REST and subsequent updates through SSE. This design extends the existing `market-streamer` and `market-http` flow from [the price-stream design](2026-10-13-market-streamer-http-design.md), assuming the changes in PR #276 are merged.

## Single Kiwoom subscription

`market-streamer` keeps its existing Kiwoom client, credentials, socket, KOSPI 200 membership load, stop time, and reconnect loop. Each connection sends one `REG` for all loaded codes with types `["0B", "0D"]`. On reconnect it sends the same registration again. A rejected registration follows the existing backoff path. No second client, socket, key, service, or setting is added.

For each `REAL` frame, the streamer handles every `data[]` entry in arrival order. `0B` entries continue through the current price and ranking path. `0D` entries become orderbook snapshots; unrelated types are ignored. A malformed `0D` entry is logged and skipped without dropping valid entries from that frame. The orderbook path must not change the price JSON, ranking keys, or price endpoint behavior.

Kiwoom identifies `0D` as 주식호가잔량. Its FID mapping is `41`–`50` for ask prices, `61`–`70` for ask quantities, `51`–`60` for bid prices, and `71`–`80` for bid quantities; FID `21` is the quote time. These mappings are also exercised in [Kiwoom's reference tests](https://github.com/Kiwoom-Securities/Kiwoom-Plugin/blob/main/tests/test_normalize.py). Level 1 is the best price on each side. Strip whitespace, take the absolute value of signed prices, and parse quantities as nonnegative integers. A zero price marks an unavailable level, regardless of its quantity; exclude that level from the stored and published arrays. Each side is filtered independently while preserving the order and quantities of valid levels. A frame missing a required level, quantity, or valid `HHMMSS` time is not a complete snapshot and is skipped. The live check below must confirm that Kiwoom sends complete snapshots, rather than incremental changes.

## Valkey and HTTP contract

The key and pub/sub channel are both `orderbook:{code}`. Each accepted snapshot is written with `SET` and published as the same JSON payload. The key has no TTL, like `price:{code}`; consumers use `timestamp` to decide whether the last known book is fresh. There is no QuestDB fallback or orderbook history.

```json
{
  "code": "005930",
  "asks": [{"price": 82000, "qty": 1200}],
  "bids": [{"price": 81900, "qty": 1500}],
  "timestamp": "2026-10-15T09:01:23+09:00"
}
```

A stored live snapshot contains zero to ten entries in each array, ordered best price first, with strictly positive prices and nonnegative quantities. `timestamp` combines FID `21` with the KST trading date, as the price stream does for FID `20`. The full arrays are replaced on every accepted update; no merging of levels is needed.

`GET /orderbooks?codes=005930,000660` reuses the existing six-character code validation and de-duplication. It pipelines one `GET` per `orderbook:{code}`, safe for Valkey cluster mode, and returns available snapshots in requested-code order. Missing codes are omitted, yielding `[]` when no snapshots exist.

`GET /orderbooks/stream?codes=005930,000660` subscribes to the requested channels before reading snapshots. It emits one `orderbook` SSE event per available snapshot, then one `orderbook` event for each published update. Clients may receive an update queued during snapshot reading afterward and use `timestamp` to order or discard older data. FastAPI's existing SSE ping and per-client pub/sub connection behavior apply. The public authentication and CORS policy remain the same as for `/prices`.

## Files and compatibility

Implementation reaches `market-streamer` parsing and streaming tests; a small `market-http` orderbook repository, controller, router registration, and tests; and API documentation in `AGENTS.md` and `README.md`. Existing Compose settings, image dependencies, environment variables, database schemas, and other services need no changes. Existing price consumers continue to receive the same payload and endpoints. The new Valkey keys are additive and do not delete existing data.

## Verification and rollout gate

- Unit tests cover all twenty price/quantity pairs, signed and padded values, invalid or partial snapshots, mixed `0B`/`0D` frames, and reconnect registration with both types.
- Valkey and HTTP tests cover `SET` plus `PUBLISH`, REST ordering and missing codes, code validation, and SSE subscription before the initial snapshot.
- Run the affected service tests, repository lint, and formatter checks. Use the dedicated `KTB_TEST_VALKEY_URL` instance for integration tests because those tests flush its database.
- Before rollout, use a real Kiwoom key during market hours to verify that one socket accepts `["0B", "0D"]` for all 200 symbols and continues delivering both feeds. Inspect representative `0D` messages to confirm all ten levels on each side and FID `21`. Record received update rate, exchange-to-receipt lag, Valkey operations, and SSE behavior under expected clients; compare price delivery with the existing `0B` stream. If the combined subscription fails or materially delays prices, revisit the socket design before rollout.

The single-socket capacity and full-snapshot behavior are assumptions until the planned live check. No second-client fallback is part of this design.
