import asyncio
from datetime import datetime

import numpy as np
from ktb_market_analyzer import Candles
from market_mcp import server
from market_mcp.settings import Settings
from mcp import Client
from mcp.types import TextContent

SETTINGS = Settings(questdb_dsn="postgresql://unused")


def call(arguments: dict) -> str:
    async def run() -> str:
        async with Client(server.build_server(SETTINGS)) as client:
            result = await client.call_tool("analyze_market", arguments)
        [content] = result.content
        assert isinstance(content, TextContent)
        return content.text

    return asyncio.run(run())


def test_analyze_market_reads_the_normalized_symbol(monkeypatch):
    calls = []

    def fake_read(dsn, timeframe, symbol, limit):
        calls.append((timeframe, symbol, limit))
        close = np.linspace(100, 150, 200)
        return Candles(high=close + 1, low=close - 1, close=close), datetime(2026, 9, 25)

    monkeypatch.setattr(server, "read_candles", fake_read)

    text = call({"symbol": "A005930", "timeframe": "1h"})

    assert calls == [("1h", "005930", 200)]
    assert text.startswith("005930 1h: 200 regular-session candles")
    assert "- rsi: " in text


def test_analyze_market_reports_missing_data(monkeypatch):
    monkeypatch.setattr(server, "read_candles", lambda *args: None)

    assert call({"symbol": "005930"}) == "No regular-session 1d candles for 005930."


def test_analyze_market_rejects_a_company_name(monkeypatch):
    def unreachable(*args):
        raise AssertionError("read_candles must not be called")

    monkeypatch.setattr(server, "read_candles", unreachable)

    assert "not a stock code" in call({"symbol": "삼성전자"})
