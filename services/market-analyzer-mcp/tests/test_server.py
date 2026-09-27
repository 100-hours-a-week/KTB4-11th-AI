"""The MCP wiring: the tool's name, its arguments, and how failures reach the model."""

import asyncio

import market_analyzer_mcp.server as server_module
import pytest
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from market_analyzer_mcp.server import build_server
from market_analyzer_mcp.settings import Settings
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from .test_analysis import candles


def settings(**over) -> Settings:
    return Settings(questdb_dsn="postgresql://localhost:8812/qdb", **over)


def test_the_server_exposes_exactly_one_tool_named_analyze_technicals():
    tools = asyncio.run(build_server(settings()).list_tools())

    assert [t.name for t in tools] == ["analyze_technicals"]


def test_an_unknown_timeframe_is_rejected_before_the_database_is_touched():
    """No read is stubbed, so reaching QuestDB would fail differently."""
    with pytest.raises(ToolError, match="unknown timeframe") as raised:
        asyncio.run(
            build_server(settings()).call_tool(
                "analyze_technicals", {"stock_code": "005930", "timeframe": "4h"}
            )
        )

    assert not isinstance(raised.value, UnexpectedToolError)


def test_an_empty_read_is_reported_rather_than_returning_nothing(monkeypatch):
    monkeypatch.setattr(server_module, "read_regular_candles", lambda *a, **k: [])

    with pytest.raises(ToolError, match="no 1d candles stored") as raised:
        asyncio.run(
            build_server(settings()).call_tool("analyze_technicals", {"stock_code": "005930"})
        )

    assert not isinstance(raised.value, UnexpectedToolError)


def test_the_configured_window_and_timeframe_reach_the_reader(monkeypatch):
    seen: dict[str, object] = {}

    def fake_read(dsn, timeframe, stock_code, *, limit=None, since=None):
        seen.update(dsn=dsn, timeframe=timeframe, stock_code=stock_code, limit=limit)
        return candles(120)

    monkeypatch.setattr(server_module, "read_regular_candles", fake_read)

    asyncio.run(
        build_server(settings(window=42)).call_tool(
            "analyze_technicals", {"stock_code": "005930", "timeframe": "15m"}
        )
    )

    assert seen == {
        "dsn": "postgresql://localhost:8812/qdb",
        "timeframe": "15m",
        "stock_code": "005930",
        "limit": 42,
    }


def test_the_model_sees_why_a_call_failed(monkeypatch):
    """An anticipated failure comes back as is_error with the reason in the content.
    A crash would carry no reason at all."""
    monkeypatch.setattr(server_module, "read_regular_candles", lambda *a, **k: [])

    async def call():
        async with Client(build_server(settings())) as client:
            return await client.call_tool("analyze_technicals", {"stock_code": "999999"})

    result = asyncio.run(call())

    assert result.is_error
    text = "\n".join(block.text for block in result.content if block.type == "text")
    assert "no 1d candles stored for '999999'" in text


def test_the_call_portfolio_builder_actually_makes_works(monkeypatch):
    """PR #42 sends the name analyze_technicals with a single stock_code argument and
    no timeframe. Pin that exact shape so a rename here breaks a test rather than
    the other service."""
    seen: dict[str, object] = {}

    def fake_read(dsn, timeframe, stock_code, *, limit=None, since=None):
        seen.update(timeframe=timeframe, stock_code=stock_code)
        return candles(120)

    monkeypatch.setattr(server_module, "read_regular_candles", fake_read)

    async def call():
        async with Client(build_server(settings())) as client:
            tools = await client.list_tools()
            result = await client.call_tool("analyze_technicals", {"stock_code": "005930"})
            return tools, result

    tools, result = asyncio.run(call())

    assert sorted(tools.tools[0].input_schema["properties"]) == ["stock_code", "timeframe"]
    assert not result.is_error
    assert seen == {"timeframe": "1d", "stock_code": "005930"}
    text = "\n".join(block.text for block in result.content if block.type == "text")
    assert "005930 1d: 120 regular-session candles" in text
    for field in DESCRIPTIONS:
        assert f"- {field}:" in text
