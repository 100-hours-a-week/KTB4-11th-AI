import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from pprint import pformat

from market_http.app import HealthResponse
from market_http.controllers.bars import Page
from market_http.controllers.indices import IndexQuote
from market_http.controllers.rankings import RankingItem, RankingResponse
from market_http.repositories.bars import Bar
from market_http.repositories.orderbooks import Orderbook, OrderbookLevel
from market_http.repositories.prices import Price
from market_http.repositories.stocks import StockDetail, StockMatch
from market_http.repositories.themes import StockTheme, Theme, ThemeGroup, ThemeStock

PRICE = Price(
    code="005930",
    price=82100,
    change=-400,
    rate=-0.48,
    volume=500,
    timestamp=datetime.fromisoformat("2026-10-15T09:01:23+09:00"),
    upper_limit_price=107200,
    lower_limit_price=57700,
)
ORDERBOOK = Orderbook(
    code="005930",
    asks=[OrderbookLevel(price=82100 + i * 100, qty=1000 + i * 10) for i in range(10)],
    bids=[OrderbookLevel(price=82000 - i * 100, qty=1200 + i * 10) for i in range(10)],
    timestamp=datetime.fromisoformat("2026-10-15T09:01:00+09:00"),
)
PAGE = Page(
    items=[
        Bar(
            timestamp=datetime.fromisoformat("2026-10-15T09:01:00+09:00"),
            open=82000,
            high=82300,
            low=81900,
            close=82100,
            volume=15000,
        )
    ],
    next_cursor="MjAyNi0xMC0xNVQwOTowMTowMCswOTowMA==",
)
INDEX = IndexQuote(
    name="KOSPI",
    value=2750.12,
    change=-12.34,
    rate=-0.45,
    timestamp=datetime.fromisoformat("2026-10-15T09:01:00+09:00"),
)
RANKING = RankingResponse(
    as_of=date(2026, 10, 15),
    updated_at=datetime.fromisoformat("2026-10-15T09:01:00+09:00"),
    items=[
        RankingItem(
            rank=1,
            code="005930",
            score=150000,
            stock_name="삼성전자",
            sector_name="전기전자",
            price=82100,
            change_rate=-0.48,
        )
    ],
)
THEME = Theme(code="T001", name="반도체")
THEME_GROUP = ThemeGroup(name="반도체", items=[Theme(code="550", name="생산")])
THEME_STOCK = ThemeStock(code="005930", name="삼성전자", is_major=True)
STOCK_THEME = StockTheme(code="T001", name="반도체", is_major=True)
STOCK_DETAIL = StockDetail(
    code="005930",
    name="삼성전자",
    english_name="SAMSUNG ELECTRONICS CO., LTD.",
    market="KOSPI",
    sector_name="전기전자",
    corp_code="00126380",
    synced_at=datetime.fromisoformat("2026-11-03T09:00:00+09:00"),
    themes=[
        StockTheme(code="515", name="스마트폰_삼성전자관련주", is_major=False),
        StockTheme(code="550", name="반도체_생산", is_major=True),
    ],
)
STOCK_MATCH = StockMatch(code="005930", name="삼성전자", sector="전기전자")
HEALTH = HealthResponse(status="ok")

ENTITIES = {
    "PRICE_EXAMPLE": PRICE,
    "ORDERBOOK_EXAMPLE": ORDERBOOK,
    "PAGE_EXAMPLE": PAGE,
    "INDEX_EXAMPLE": INDEX,
    "RANKING_EXAMPLE": RANKING,
    "THEME_EXAMPLE": THEME,
    "THEME_GROUP_EXAMPLE": THEME_GROUP,
    "THEME_STOCK_EXAMPLE": THEME_STOCK,
    "STOCK_THEME_EXAMPLE": STOCK_THEME,
    "STOCK_DETAIL_EXAMPLE": STOCK_DETAIL,
    "STOCK_MATCH_EXAMPLE": STOCK_MATCH,
    "HEALTH_EXAMPLE": HEALTH,
}


if __name__ == "__main__":
    output = Path(sys.argv[1])
    content = "\n\n".join(
        f"{name} = {pformat(entity.model_dump(mode='json'), sort_dicts=False, width=88)}"
        for name, entity in ENTITIES.items()
    )
    output.write_text(f"{content}\n")
    subprocess.run(["ruff", "format", str(output)], check=True)
