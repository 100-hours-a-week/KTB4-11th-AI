from datetime import datetime

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from market_http.openapi_examples import STOCK_DETAIL_EXAMPLE, STOCK_MATCH_EXAMPLE
from market_http.repositories.themes import StockTheme


class StockDetail(BaseModel):
    code: str
    name: str
    english_name: str | None
    market: str
    sector_name: str
    corp_code: str
    synced_at: datetime
    themes: list[StockTheme]

    model_config = ConfigDict(json_schema_extra={"examples": [STOCK_DETAIL_EXAMPLE]})


class StockMatch(BaseModel):
    code: str
    name: str

    model_config = ConfigDict(json_schema_extra={"examples": [STOCK_MATCH_EXAMPLE]})


def list_kospi200_names(engine: sa.Engine) -> list[tuple[str, str, str | None]]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT c.stock_code, c.name, c.eng_name FROM corporations c"
                " JOIN corporation_indices ci ON ci.stock_code = c.stock_code"
                " WHERE ci.index_name = 'KOSPI200'"
            )
        )
        return [tuple(row) for row in rows]


def find_stock(engine: sa.Engine, stock_code: str) -> StockDetail | None:
    with engine.connect() as conn:
        stock = (
            conn.execute(
                sa.text(
                    "SELECT stock_code AS code, name, eng_name AS english_name, market,"
                    " sector_name, corp_code, synced_at"
                    " FROM corporations WHERE stock_code = :code"
                ),
                {"code": stock_code},
            )
            .mappings()
            .first()
        )
        if stock is None:
            return None
        themes = conn.execute(
            sa.text(
                "SELECT t.theme_code AS code, t.name, tc.is_major"
                " FROM theme_corporations tc"
                " JOIN themes t ON t.theme_code = tc.theme_code"
                " WHERE tc.stock_code = :code"
                " ORDER BY t.theme_code"
            ),
            {"code": stock_code},
        ).mappings()
        return StockDetail.model_validate({**stock, "themes": list(themes)})
