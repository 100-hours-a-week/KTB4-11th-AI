"""MCP server over streamable HTTP at /mcp, exposing one tool: analyze_technicals."""

import logging

import numpy as np
from ktb_core.logging import setup_logging
from ktb_market_analyzer import Candles, interpret
from ktb_market_analyzer.descriptions import DESCRIPTIONS
from ktb_market_reader import TIMEFRAME_TABLES, Candle, read_regular_candles
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from market_analyzer_mcp.settings import Settings

__all__ = ["analyze", "build_server", "format_reading", "main"]

log = logging.getLogger(__name__)

FIELDS = tuple(DESCRIPTIONS)


def format_reading(field: str, reading) -> str:
    """One indicator as a line the model reads, or a line saying why there is none."""
    if reading.value is None:
        return f"- {field}: not computable from the candles read ({reading.description})"
    parts = [f"- {field}: {reading.value:.4f}"]
    if reading.comment is not None:
        parts.append(f"verdict {reading.comment} — {reading.comment_reasoning}")
    parts.append(reading.description)
    return " | ".join(parts)


def analyze(candles: list[Candle], symbol: str, timeframe: str) -> str:
    """Read every indicator over ``candles`` and render the result as text."""
    series = Candles(
        high=np.array([c.high for c in candles], dtype=np.float64),
        low=np.array([c.low for c in candles], dtype=np.float64),
        close=np.array([c.close for c in candles], dtype=np.float64),
    )
    header = (
        f"{symbol} {timeframe}: {len(candles)} regular-session candles, "
        f"{candles[0].ts.isoformat()} to {candles[-1].ts.isoformat()}, "
        f"last close {candles[-1].close:.4f}"
    )
    lines = [format_reading(field, interpret(field, series)) for field in FIELDS]
    return "\n".join([header, *lines])


def build_server(settings: Settings) -> MCPServer:
    server = MCPServer(
        name="market-analyzer-mcp",
        version="0.1.0",
        instructions=(
            "Technical indicators and their verdicts for one listed symbol, computed "
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


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    server = build_server(settings)
    log.info("market-analyzer-mcp listening on %s:%d/mcp", settings.host, settings.port)
    server.run(transport="streamable-http", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
