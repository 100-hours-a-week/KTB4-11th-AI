"""The MCP wiring: the tool's name, its arguments, and what the model gets back."""

import asyncio

import market_analyzer_mcp.server as server_module
from ktb_market_analyzer import Candles
from market_analyzer_mcp.server import build_app, build_server
from market_analyzer_mcp.settings import Settings
from mcp import Client

from .test_analysis import NEWEST, series


def settings(**over) -> Settings:
    return Settings(questdb_dsn="postgresql://localhost:8812/qdb", **over)


def found(count: int = 120) -> tuple[Candles, object]:
    return series(count), NEWEST


def call(server, arguments):
    async def go():
        async with Client(server) as client:
            return await client.call_tool("analyze_technicals", arguments)

    return asyncio.run(go())


def text_of(result) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


def test_the_server_exposes_exactly_one_tool_named_analyze_technicals():
    tools = asyncio.run(build_server(settings()).list_tools())

    assert [t.name for t in tools] == ["analyze_technicals"]


def test_the_schema_offers_only_the_four_known_timeframes():
    async def go():
        async with Client(build_server(settings())) as client:
            return await client.list_tools()

    tools = asyncio.run(go())
    schema = tools.tools[0].input_schema

    assert sorted(schema["properties"]) == ["stock_code", "timeframe"]
    assert schema["properties"]["timeframe"]["enum"] == ["1m", "15m", "1h", "1d"]


def test_the_call_portfolio_builder_actually_makes_works(monkeypatch):
    """PR #42 sends the name analyze_technicals with a single stock_code argument and
    no timeframe. Pin that shape so a rename breaks a test rather than the other
    service."""
    seen: dict[str, object] = {}

    def fake_read(dsn, timeframe, symbol, limit):
        seen.update(timeframe=timeframe, symbol=symbol, limit=limit)
        return found()

    monkeypatch.setattr(server_module, "read_candles", fake_read)

    result = call(build_server(settings()), {"stock_code": "005930"})

    assert not result.is_error
    assert seen == {"timeframe": "1d", "symbol": "005930", "limit": 200}
    assert "005930 1d:" in text_of(result)


def test_a_prefixed_or_suffixed_code_is_normalised_before_the_query(monkeypatch):
    seen: dict[str, object] = {}

    def fake_read(dsn, timeframe, symbol, limit):
        seen["symbol"] = symbol
        return found()

    monkeypatch.setattr(server_module, "read_candles", fake_read)

    call(build_server(settings()), {"stock_code": "A005930.KS"})

    assert seen["symbol"] == "005930"


def test_a_company_name_gets_guidance_rather_than_a_query(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("the database must not be touched")

    monkeypatch.setattr(server_module, "read_candles", fail)

    result = call(build_server(settings()), {"stock_code": "삼성전자"})

    assert not result.is_error
    assert "not a stock code" in text_of(result)


def test_a_symbol_with_no_candles_says_so(monkeypatch):
    monkeypatch.setattr(server_module, "read_candles", lambda *a, **k: None)

    result = call(build_server(settings()), {"stock_code": "999999"})

    assert not result.is_error
    assert "No 1d candles stored for 999999" in text_of(result)


def test_a_resampled_timeframe_is_reported_as_mixing_sessions(monkeypatch):
    monkeypatch.setattr(server_module, "read_candles", lambda *a, **k: found())

    regular = text_of(call(build_server(settings()), {"stock_code": "005930"}))
    mixed = text_of(call(build_server(settings()), {"stock_code": "005930", "timeframe": "15m"}))

    assert "(regular session)" in regular
    assert "(all sessions)" in mixed


def test_the_configured_candle_limit_reaches_the_reader(monkeypatch):
    seen: dict[str, object] = {}

    def fake_read(dsn, timeframe, symbol, limit):
        seen["limit"] = limit
        return found()

    monkeypatch.setattr(server_module, "read_candles", fake_read)

    call(build_server(settings(candle_limit=42)), {"stock_code": "005930"})

    assert seen["limit"] == 42


def test_the_app_serves_mcp_and_health():
    routes = {getattr(r, "path", None) for r in build_app(settings()).routes}

    assert "/health" in routes
    assert any(p and p.startswith("/mcp") for p in routes)


def test_dns_rebinding_protection_is_off_so_a_service_name_host_is_accepted():
    """Measured: with a loopback bind the SDK answers Host: market-analyzer-mcp with
    421 Invalid Host header. portfolio-builder reaches this server by service name."""
    from starlette.testclient import TestClient

    with TestClient(build_app(settings()), base_url="http://market-analyzer-mcp:8000") as client:
        assert client.get("/health").status_code == 200
