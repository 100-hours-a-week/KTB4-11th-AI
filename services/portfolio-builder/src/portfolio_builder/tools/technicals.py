from functools import cache
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from ktb_core.normalize import normalize
from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import Field

from portfolio_builder.errors import NoMarketData, UnknownCompany
from portfolio_builder.evidence import compute_evidence
from portfolio_builder.tools import to_json

TIMEFRAME_HELP = (
    "bar size: 1m or 15m for intraday momentum and volume spikes, 1h for the last few sessions,"
    " 1d for multi-week trend, 52-week position, 12-month momentum, volatility and liquidity"
    " regimes and KOSPI 200 cross-section ranks (daily only)"
)


def _resolve_company(engine: sa.Engine, name: str) -> dict[str, Any]:
    with engine.connect() as conn:
        company = (
            conn.execute(
                sa.text(
                    "SELECT corp_code, corp_name, stock_code FROM ("
                    "  SELECT c.corp_code, c.corp_name, c.stock_code, 0 AS priority"
                    "  FROM companies c WHERE c.corp_code = :code"
                    "  UNION ALL"
                    "  SELECT c.corp_code, c.corp_name, c.stock_code, 1"
                    "  FROM company_aliases a JOIN companies c ON c.corp_code = a.corp_code"
                    "  WHERE a.alias = :alias"
                    ") AS matches ORDER BY priority LIMIT 1"
                ),
                {"code": name.strip(), "alias": normalize(name)},
            )
            .mappings()
            .first()
        )
        if company is not None:
            return dict(company)
        escaped = name.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        candidates = list(
            conn.execute(
                sa.text(
                    "SELECT corp_name FROM companies WHERE corp_name ILIKE :pattern"
                    " ORDER BY corp_name LIMIT 5"
                ),
                {"pattern": f"%{escaped}%"},
            ).scalars()
        )
    raise UnknownCompany(
        f'no company matches "{name}". Candidates: {", ".join(candidates) or "none"}'
    )


def technicals_tool(engine: sa.Engine, market: Any) -> BaseTool:
    universe = cache(market.universe_closes)

    @tool(
        "analyze_technicals",
        description=(
            "Technical evidence computed from one listed company's OHLCV bars at the timeframe"
            " you choose: returns, trend, breakout, 52-week position, volatility, volume and"
            " liquidity, each with its value; anything that cannot be computed is listed under"
            " unavailable with the reason. Look the company up by name or company_id."
        ),
    )
    def analyze_technicals(
        name: Annotated[str, Field(description="company name or company_id")],
        timeframe: Annotated[Literal["1m", "15m", "1h", "1d"], Field(description=TIMEFRAME_HELP)],
    ) -> str:
        company = _resolve_company(engine, name)
        bars, as_of = market.bars(company["stock_code"], timeframe)
        if bars.close.size == 0:
            raise NoMarketData(
                f"no {timeframe} bars for {company['corp_name']} (stock_code"
                f" {company['stock_code']}); it is outside the KOSPI 200 archive or not"
                " collected yet"
            )
        evidence = compute_evidence(timeframe, bars, universe() if timeframe == "1d" else None)
        return to_json(
            {
                "company": company["corp_name"],
                "company_id": company["corp_code"],
                "stock_code": company["stock_code"],
                "timeframe": timeframe,
                "as_of": as_of,
                "bars": int(bars.close.size),
                "evidence": evidence.values,
                "unavailable": evidence.unavailable,
            }
        )

    return analyze_technicals
