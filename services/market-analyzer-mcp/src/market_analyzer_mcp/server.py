from typing import Literal

from mcp.server import MCPServer

from market_analyzer_mcp.analysis import describe, normalize_symbol, read_candles
from market_analyzer_mcp.settings import Settings


def build_server(settings: Settings) -> MCPServer:
    mcp = MCPServer("market-analyzer-mcp")

    @mcp.tool()
    def analyze_market(symbol: str, timeframe: Literal["1m", "15m", "1h", "1d"] = "1d") -> str:
        """Technical indicators (RSI, MACD, Stochastic, ROC, Williams %R) for a KRX stock.

        ``symbol`` is the six-character stock code, e.g. 005930. Each indicator comes with
        its latest value, a verdict with the reasoning behind it, and what it measures.
        """
        code = normalize_symbol(symbol)
        if code is None:
            return f"{symbol!r} is not a stock code; pass the six-character code, e.g. 005930."
        found = read_candles(settings.questdb_dsn, timeframe, code, settings.candle_limit)
        if found is None:
            return f"No regular-session {timeframe} candles for {code}."
        candles, newest = found
        return describe(code, timeframe, candles, newest)

    return mcp
