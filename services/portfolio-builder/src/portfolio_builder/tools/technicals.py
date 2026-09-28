from collections.abc import Callable, Mapping
from functools import cache
from typing import Any, Literal

import sqlalchemy as sa
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.company import resolve_company
from portfolio_builder.errors import NoMarketData
from portfolio_builder.interpretation import interpret
from portfolio_builder.measurement import Array, measure
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.result import json_result

TIMEFRAME_HELP = (
    "bar size: 1m or 15m for intraday momentum and volume spikes, 1h for the last few sessions,"
    " 1d for multi-week trend, 52-week position, 12-month momentum, volatility and liquidity"
    " regimes and KOSPI 200 cross-section ranks (daily only)"
)


class AnalyzeTechnicalsArgs(BaseModel):
    name: str = Field(description="company name or company_id")
    timeframe: Literal["1m", "15m", "1h", "1d"] = Field(description=TIMEFRAME_HELP)


def analyze_technicals(
    name: str,
    timeframe: str,
    *,
    engine: sa.Engine,
    market: Any,
    universe: Callable[[], Mapping[str, Array]],
) -> str:
    company = resolve_company(engine, name)
    bars, as_of = market.bars(company.stock_code, timeframe)
    if bars.close.size == 0:
        raise NoMarketData(
            f"no {timeframe} bars for {company.corp_name} (stock_code {company.stock_code});"
            " it is outside the KOSPI 200 archive or not collected yet"
        )
    measurements = measure(timeframe, bars, universe() if timeframe == "1d" else None)
    signals = interpret(measurements)
    return json_result(
        {
            "company": company.corp_name,
            "company_id": company.corp_code,
            "stock_code": company.stock_code,
            "timeframe": timeframe,
            "as_of": as_of,
            "bars": int(bars.close.size),
            "signals": {name: signal._asdict() for name, signal in signals.signals.items()},
            "unavailable": signals.unavailable,
        }
    )


def technicals_tool(engine: sa.Engine, market: Any) -> BaseTool:
    return StructuredTool.from_function(
        bind(
            analyze_technicals,
            engine=engine,
            market=market,
            universe=cache(market.universe_closes),
        ),
        name="analyze_technicals",
        description=(
            "Technical signals for one listed company from its OHLCV bars at the timeframe you"
            " choose. Each signal (trend, short_term_move, breakout, year_range,"
            " relative_strength, short_term_rank, volatility, volume, liquidity) has a state"
            " decided by a fixed rule and the measurements behind it; weigh the signals"
            " together yourself. Signals that cannot be decided are listed under unavailable"
            " with the reason. Look the company up by name or company_id."
        ),
        args_schema=AnalyzeTechnicalsArgs,
    )
