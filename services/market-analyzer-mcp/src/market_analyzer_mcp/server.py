"""The MCP server and its one tool."""

import logging
from typing import Literal

from mcp.server import MCPServer

from market_analyzer_mcp.analysis import describe, normalize_symbol
from market_analyzer_mcp.candles import TIMEFRAMES, read_candles
from market_analyzer_mcp.settings import Settings

__all__ = ["build_server"]

log = logging.getLogger(__name__)


def build_server(settings: Settings) -> MCPServer:
    mcp = MCPServer("market-analyzer-mcp", version="0.1.0")

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
