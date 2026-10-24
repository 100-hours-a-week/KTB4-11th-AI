import asyncio
import time
from datetime import UTC, datetime

import pytest
from market_http.controllers import orderbooks, prices
from market_http.market_subscription import MarketSubscription
from market_http.repositories.prices import TickPrice

from .example_entities import ORDERBOOK, PRICE
from .test_market_subscription import FakeValkey


@pytest.mark.parametrize("feed", ["price", "orderbook"])
def test_shared_stream_registers_before_snapshot_and_preserves_bursts_and_hooks(monkeypatch, feed):
    async def scenario():
        valkey = FakeValkey()
        subscription = MarketSubscription(valkey)
        model = PRICE if feed == "price" else ORDERBOOK
        live = model.model_dump_json()
        controller = prices if feed == "price" else orderbooks
        deliveries = []
        traces = []
        snapshot_completed = []
        monkeypatch.setattr(controller, "track_delivery", lambda **kw: deliveries.append(kw))
        monkeypatch.setattr(
            controller,
            "track_event",
            lambda raw=None, **kw: traces.append((raw, kw)),
            raising=False,
        )

        async def snapshot(*args):
            assert len(valkey.pubsubs) == 1
            for _ in range(3):
                await valkey.pubsubs[0].messages.put(
                    {"type": "pmessage", "channel": f"{feed}:005930", "data": live}
                )
            await asyncio.sleep(0)
            snapshot_completed.append((time.monotonic(), datetime.now(UTC)))
            return [model]

        monkeypatch.setattr(
            controller, f"current_{'prices' if feed == 'price' else 'orderbooks'}", snapshot
        )
        events = (
            prices.stream_prices(valkey, "conf", ["005930"], subscription)
            if feed == "price"
            else orderbooks.stream_orderbooks(valkey, ["005930"], subscription)
        )
        try:
            first = await asyncio.wait_for(anext(events), 1)
            assert first.data == model
            expected = TickPrice.model_validate_json(live) if feed == "price" else model
            for _ in range(3):
                event = await asyncio.wait_for(anext(events), 1)
                assert event.event == feed
                assert event.data == expected
            assert deliveries[0] == {}
            assert traces[0] == (None, {})
            for delivery, (raw, trace) in zip(deliveries[1:], traces[1:], strict=True):
                assert delivery["live"] is True
                assert delivery["received_at"] <= snapshot_completed[0][0]
                assert trace["received"] <= snapshot_completed[0][1]
                assert raw == live
            await subscription.stop()
            with pytest.raises(StopAsyncIteration):
                await anext(events)
        finally:
            await events.aclose()
            await subscription.stop()

    asyncio.run(scenario())
