from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from ktb_market_reader import Candle
from market_analyzer_mcp.__main__ import FIELDS, analyze, build_server, format_reading
from market_analyzer_mcp.settings import Settings
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

TS = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)


def _candles(count: int) -> list[Candle]:
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.normal(0, 0.4, count))
    return [
        Candle(
            ts=TS + timedelta(days=i),
            high=float(close[i]) + 0.3,
            low=float(close[i]) - 0.3,
            close=float(close[i]),
            indicators={},
        )
        for i in range(count)
    ]


def _settings(**over) -> Settings:
    return Settings(questdb_dsn="postgresql://localhost:8812/qdb", **over)


def test_every_documented_indicator_is_reported():
    text = analyze(_candles(120), "005930", "1d")

    for field in DESCRIPTIONS:
        assert f"- {field}:" in text


def test_the_field_set_is_the_analyzers_catalogue_not_a_copy():
    """Adding an indicator to the analyzer must not need an edit here."""
    assert FIELDS == tuple(DESCRIPTIONS)


def test_the_header_names_the_symbol_timeframe_and_window():
    candles = _candles(120)
    text = analyze(candles, "005930", "1d").splitlines()[0]

    assert "005930" in text
    assert "1d" in text
    assert "120 regular-session candles" in text
    assert candles[-1].ts.isoformat() in text


def test_a_short_window_says_so_rather_than_inventing_a_number():
    """Ten candles is below every indicator's warm-up, so each line must say so."""
    text = analyze(_candles(10), "005930", "1d")

    for field in DESCRIPTIONS:
        assert f"- {field}: not computable" in text


def test_a_value_carries_its_verdict_and_the_reason_for_it():
    text = analyze(_candles(120), "005930", "1d")
    rsi_line = next(line for line in text.splitlines() if line.startswith("- rsi:"))

    assert "verdict" in rsi_line
    assert rsi_line.count("|") >= 2


def test_macd_signal_reports_a_value_without_a_verdict():
    """macd_signal deliberately carries no verdict rule; the line must still show it."""
    text = analyze(_candles(120), "005930", "1d")
    line = next(line for line in text.splitlines() if line.startswith("- macd_signal:"))

    assert "not computable" not in line
    assert "verdict" not in line


def test_format_reading_renders_a_missing_value_without_crashing():
    class Missing:
        value = None
        comment = None
        comment_reasoning = None
        description = "some description"

    assert format_reading("rsi", Missing()) == (
        "- rsi: not computable from the candles read (some description)"
    )


def test_the_server_exposes_exactly_one_tool_named_analyze_market():
    import asyncio

    server = build_server(_settings())
    tools = asyncio.run(server.list_tools())

    assert [t.name for t in tools] == ["analyze_market"]


def test_the_tool_rejects_an_unknown_timeframe_before_touching_the_database():
    """A DSN that cannot connect proves the check runs before any query."""
    import asyncio

    server = build_server(_settings())

    with pytest.raises(ToolError, match="unknown timeframe") as raised:
        asyncio.run(server.call_tool("analyze_market", {"symbol": "005930", "timeframe": "4h"}))
    assert not isinstance(raised.value, UnexpectedToolError)


def test_the_tool_reports_an_empty_result_rather_than_returning_nothing(monkeypatch):
    import asyncio

    import market_analyzer_mcp.__main__ as mod

    monkeypatch.setattr(mod, "read_regular_candles", lambda *a, **k: [])
    server = build_server(_settings())

    with pytest.raises(ToolError, match="no 1d candles stored") as raised:
        asyncio.run(server.call_tool("analyze_market", {"symbol": "005930"}))
    assert not isinstance(raised.value, UnexpectedToolError)


def test_the_tool_reads_the_configured_window(monkeypatch):
    import asyncio

    import market_analyzer_mcp.__main__ as mod

    seen: dict[str, object] = {}

    def fake_read(dsn, timeframe, symbol, *, limit=None, since=None):
        seen.update(dsn=dsn, timeframe=timeframe, symbol=symbol, limit=limit)
        return _candles(120)

    monkeypatch.setattr(mod, "read_regular_candles", fake_read)
    server = build_server(_settings(window=42))

    asyncio.run(server.call_tool("analyze_market", {"symbol": "005930", "timeframe": "15m"}))

    assert seen == {
        "dsn": "postgresql://localhost:8812/qdb",
        "timeframe": "15m",
        "symbol": "005930",
        "limit": 42,
    }


def test_the_default_timeframe_is_daily(monkeypatch):
    import asyncio

    import market_analyzer_mcp.__main__ as mod

    seen: dict[str, object] = {}

    def fake_read(dsn, timeframe, symbol, *, limit=None, since=None):
        seen["timeframe"] = timeframe
        return _candles(120)

    monkeypatch.setattr(mod, "read_regular_candles", fake_read)
    server = build_server(_settings())

    asyncio.run(server.call_tool("analyze_market", {"symbol": "005930"}))

    assert seen["timeframe"] == "1d"


def test_the_model_sees_why_a_call_failed(monkeypatch):
    """Through a real client an anticipated failure comes back as is_error with the
    reason in the content. A crash would carry no reason at all."""
    import asyncio

    import market_analyzer_mcp.__main__ as mod
    from mcp import Client

    monkeypatch.setattr(mod, "read_regular_candles", lambda *a, **k: [])
    server = build_server(_settings())

    async def call():
        async with Client(server) as client:
            return await client.call_tool("analyze_market", {"symbol": "999999"})

    result = asyncio.run(call())

    assert result.is_error
    text = "\n".join(block.text for block in result.content if block.type == "text")
    assert "no 1d candles stored for '999999'" in text


def test_a_successful_call_returns_the_reading_through_a_real_client(monkeypatch):
    import asyncio

    import market_analyzer_mcp.__main__ as mod
    from mcp import Client

    monkeypatch.setattr(mod, "read_regular_candles", lambda *a, **k: _candles(120))
    server = build_server(_settings())

    async def call():
        async with Client(server) as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "analyze_market", {"symbol": "005930", "timeframe": "1d"}
            )
            return tools, result

    tools, result = asyncio.run(call())

    assert [t.name for t in tools.tools] == ["analyze_market"]
    assert sorted(tools.tools[0].input_schema["properties"]) == ["symbol", "timeframe"]
    assert not result.is_error
    text = "\n".join(block.text for block in result.content if block.type == "text")
    assert "005930 1d: 120 regular-session candles" in text
    for field in DESCRIPTIONS:
        assert f"- {field}:" in text
