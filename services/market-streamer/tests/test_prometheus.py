import asyncio
import math
import urllib.request

import pytest
from market_streamer.observability import metrics as module
from market_streamer.observability.metrics import MarketMetrics
from market_streamer.settings import Settings
from market_streamer.stream import stream_market_data


def test_raw_events_include_invalid_duplicates_and_age_grows_during_silence(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    metrics = MarketMetrics()
    value = metrics.registry.get_sample_value
    assert math.isnan(value("market_streamer_market_data_age_seconds", {"kind": "price"}))
    metrics.receive([{"type": "0B"}, {"type": "0B"}, {"type": "0D"}, {"type": "REG"}, "invalid"])
    assert value("market_streamer_market_events_received_total", {"kind": "price"}) == 2
    assert value("market_streamer_market_events_received_total", {"kind": "orderbook"}) == 1
    assert value("market_streamer_market_events_received_bytes_total", {"kind": "price"}) == 26
    clock[0] = 107.5
    assert value("market_streamer_market_data_age_seconds", {"kind": "price"}) == 7.5
    assert value("market_streamer_market_data_age_seconds", {"kind": "orderbook"}) == 7.5


def test_stream_counts_entire_raw_frame_before_publishing(monkeypatch):
    metrics = MarketMetrics()

    class Client:
        async def subscribe(self, **kwargs):
            pass

        async def iter_messages(self):
            yield {"trnm": "REAL", "data": [{"type": "0B"}, {"type": "0D"}]}
            assert (
                metrics.registry.get_sample_value(
                    "market_streamer_market_events_received_total", {"kind": "price"}
                )
                == 1
            )
            assert (
                metrics.registry.get_sample_value(
                    "market_streamer_market_events_received_total", {"kind": "orderbook"}
                )
                == 1
            )
            raise asyncio.CancelledError

        async def close(self):
            pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stream_market_data(Client(), None, [], metrics=metrics))


def test_listener_serves_metrics_and_closes_after_exception():
    metrics = MarketMetrics()
    with pytest.raises(ValueError):
        with metrics.serve("127.0.0.1", 0) as server:
            address = server.server_address
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{address[1]}/metrics", timeout=2) as response:
                assert b"market_streamer_market_events_received_total" in response.read()
            raise ValueError("stopping")
    assert server.socket.fileno() == -1


def test_prometheus_bind_settings_from_environment(monkeypatch):
    required = dict(
        postgres_dsn="unused",
        kiwoom_app_key="fake",
        kiwoom_secret_key="fake",
        valkey_host="localhost",
    )
    assert Settings(**required).prometheus_bind_host == "127.0.0.1"
    monkeypatch.setenv("MARKET_STREAMER_PROMETHEUS_BIND_HOST", "10.0.0.2")
    monkeypatch.setenv("MARKET_STREAMER_PROMETHEUS_PORT", "9102")
    settings = Settings(**required)
    assert (settings.prometheus_bind_host, settings.prometheus_port) == ("10.0.0.2", 9102)
