"""The MCP server and its one tool. This is where QuestDB is read."""

import logging

from ktb_market_reader import TIMEFRAME_TABLES, read_regular_candles
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from market_analyzer_mcp.analysis import analyze
from market_analyzer_mcp.settings import Settings

__all__ = ["build_server"]

log = logging.getLogger(__name__)


def build_server(settings: Settings) -> MCPServer:
    server = MCPServer(
        name="market-analyzer-mcp",
        version="0.1.0",
        instructions=(
            "Technical indicators and their verdicts for one listed company, computed "
            "from QuestDB candles. Call analyze_technicals with a six-character stock code."
        ),
    )

    @server.tool()
    def analyze_technicals(stock_code: str, timeframe: str = "1d") -> str:
        """Technical indicators and their verdicts for one listed company.

        ``stock_code`` is the six-character stock code (for example ``005930``).
        ``timeframe`` is one of ``1m``, ``15m``, ``1h`` or ``1d``, and defaults to
        daily -- portfolio-builder calls this with ``stock_code`` alone.
        """
        # ToolError, not ValueError: the SDK puts a ToolError's message in the
        # result for the model to read, and hides anything else behind a generic
        # "Error executing tool". The model needs to know which of these it hit.
        if timeframe not in TIMEFRAME_TABLES:
            raise ToolError(
                f"unknown timeframe {timeframe!r}; expected one of {sorted(TIMEFRAME_TABLES)}"
            )

        candles = read_regular_candles(
            settings.questdb_dsn, timeframe, stock_code, limit=settings.window
        )
        if not candles:
            raise ToolError(
                f"no {timeframe} candles stored for {stock_code!r}; "
                "the collector may not have reached this symbol yet"
            )

        log.info("analyze_technicals %s %s over %d candles", stock_code, timeframe, len(candles))
        return analyze(candles, stock_code, timeframe)

    return server
