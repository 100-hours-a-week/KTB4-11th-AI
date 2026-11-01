import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from market_http.openapi_examples import STOCK_THEME_EXAMPLE, THEME_EXAMPLE, THEME_STOCK_EXAMPLE


class Theme(BaseModel):
    code: str
    name: str

    model_config = ConfigDict(json_schema_extra={"examples": [THEME_EXAMPLE]})


class ThemeStock(BaseModel):
    code: str
    name: str
    is_major: bool

    model_config = ConfigDict(json_schema_extra={"examples": [THEME_STOCK_EXAMPLE]})


class StockTheme(BaseModel):
    code: str
    name: str
    is_major: bool

    model_config = ConfigDict(json_schema_extra={"examples": [STOCK_THEME_EXAMPLE]})


def list_themes(engine: sa.Engine) -> list[Theme]:
    statement = sa.text(
        "SELECT t.theme_code AS code, t.name FROM themes t"
        " LEFT JOIN theme_companies tc ON tc.theme_code = t.theme_code"
        " GROUP BY t.theme_code, t.name"
        " ORDER BY COUNT(tc.stock_code) DESC, t.theme_code"
    )
    with engine.connect() as conn:
        return [Theme.model_validate(row) for row in conn.execute(statement).mappings()]


def find_theme_stocks(
    engine: sa.Engine, theme_code: str, limit: int, offset: int
) -> list[ThemeStock] | None:
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM themes WHERE theme_code = :code"), {"code": theme_code}
        ).first()
        if exists is None:
            return None
        rows = conn.execute(
            sa.text(
                "SELECT tc.stock_code AS code, c.name, tc.is_major"
                " FROM theme_companies tc"
                " JOIN corporations c ON c.stock_code = tc.stock_code"
                " LEFT JOIN corporation_indices ci"
                " ON ci.stock_code = tc.stock_code AND ci.index_name = 'KOSPI200'"
                " WHERE tc.theme_code = :code"
                " ORDER BY tc.is_major DESC, ci.stock_code IS NULL, tc.stock_code"
                " LIMIT :limit OFFSET :offset"
            ),
            {"code": theme_code, "limit": limit, "offset": offset},
        ).mappings()
        return [ThemeStock.model_validate(row) for row in rows]


def find_stock_themes(engine: sa.Engine, stock_code: str) -> list[StockTheme] | None:
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM corporations WHERE stock_code = :code"), {"code": stock_code}
        ).first()
        if exists is None:
            return None
        rows = conn.execute(
            sa.text(
                "SELECT t.theme_code AS code, t.name, tc.is_major"
                " FROM theme_companies tc"
                " JOIN themes t ON t.theme_code = tc.theme_code"
                " WHERE tc.stock_code = :code"
                " ORDER BY tc.is_major DESC,"
                " (SELECT COUNT(*) FROM theme_companies m WHERE m.theme_code = tc.theme_code) DESC,"
                " t.theme_code"
            ),
            {"code": stock_code},
        ).mappings()
        return [StockTheme.model_validate(row) for row in rows]
