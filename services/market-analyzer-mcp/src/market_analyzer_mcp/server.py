"""The MCP server, its one tool, and the ASGI app that serves them."""

import logging
from typing import Literal

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from market_analyzer_mcp.analysis import describe, normalize_symbol
from market_analyzer_mcp.candles import TIMEFRAMES, read_candles
from market_analyzer_mcp.settings import Settings

__all__ = ["build_app", "build_server"]

log = logging.getLogger(__name__)


def build_server(settings: Settings) -> MCPServer:
    mcp = MCPServer("market-analyzer-mcp")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @mcp.tool()
    def analyze_technicals(
        stock_code: str, timeframe: Literal["1m", "15m", "1h", "1d"] = "1d"
    ) -> str:
        """Technical indicators (RSI, MACD, Stochastic, ROC, Williams %R) for a KRX stock.

        ``stock_code`` is the six-character stock code, e.g. 005930. Each indicator
        comes with its latest value, a verdict with the reasoning behind it, and what
        the indicator measures.
        """
        code = normalize_symbol(stock_code)
        if code is None:
            return f"{stock_code!r} is not a stock code; pass the six-character code, e.g. 005930."

        found = read_candles(settings.questdb_dsn, timeframe, code, settings.candle_limit)
        if found is None:
            return f"No {timeframe} candles stored for {code}."

        candles, newest = found
        log.info("analyze_technicals %s %s over %d candles", code, timeframe, candles.close.size)
        return describe(code, timeframe, candles, newest, sessions_mixed=not TIMEFRAMES[timeframe])

    return mcp


def build_app(settings: Settings) -> Starlette:
    # The SDK turns DNS-rebinding protection on when host is a loopback address, which
    # would reject portfolio-builder calling market-analyzer-mcp:8000 by service name.
    # Measured: a loopback bind answers such a request with 421 Invalid Host header.
    # Pin it off; the server is never published outside the Docker network.
    return build_server(settings).streamable_http_app(
        host=settings.host,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
