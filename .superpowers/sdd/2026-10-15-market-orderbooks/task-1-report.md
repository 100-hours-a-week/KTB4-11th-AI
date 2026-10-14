# Task 1 implementation report

## RED

Added the specified parser, frame filtering, and Valkey publication tests first. Ran:

```text
rtk proxy env UV_CACHE_DIR=/tmp/ktb-268-uv-cache uv run pytest services/market-streamer/tests/test_stream.py -q
```

Collection failed with `ImportError: cannot import name 'frame_orderbooks'`, because the requested interfaces did not yet exist.

## GREEN

Implemented `parse_orderbook`, `frame_orderbooks`, and `publish_orderbooks` in `stream.py`. The parser reads all ten ask and bid price/quantity FID pairs, strips padded values, takes absolute prices, rejects negative quantities and incomplete or invalid values, and builds a KST timestamp from FID 21. The frame parser skips malformed/non-orderbook entries and logs malformed orderbooks. The publisher pipelines matching `SET` and `PUBLISH` commands on `orderbook:{code}`.

Final command:

```text
rtk proxy env UV_CACHE_DIR=/tmp/ktb-268-uv-cache uv run pytest services/market-streamer -q
```

Result: **20 passed, 2 skipped**. The two Valkey integration tests were skipped because `KTB_TEST_VALKEY_URL` is unset.

Additional checks passed:

```text
uv run ruff check services/market-streamer/src/market_streamer/stream.py services/market-streamer/tests/test_stream.py
uv run ruff format --check services/market-streamer/src/market_streamer/stream.py services/market-streamer/tests/test_stream.py
git diff --check
```

## Self-review

- Changed only `services/market-streamer/src/market_streamer/stream.py` and `services/market-streamer/tests/test_stream.py`.
- Existing price parsing, price publication, ranking, and stream behavior are untouched.
- Tests cover all twenty levels, padded signed prices, KST timestamp construction, malformed/missing/blank fields, negative quantities, mixed `0B`/`0D` frames, and identical Valkey storage/pubsub payloads.
- No dependency, configuration, or schema changes were needed.

## Concerns

The real Valkey integration assertions did not execute because the dedicated test URL is not configured. The combined Kiwoom subscription and live full-snapshot behavior also require the design's planned market-hours check; stream wiring is outside Task 1.
