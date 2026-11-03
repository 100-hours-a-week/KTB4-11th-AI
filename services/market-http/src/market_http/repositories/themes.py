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


def list_themes(engine: sa.Engine) -> list[tuple[Theme, list[str]]]:
    with engine.connect() as conn:
        themes = conn.execute(
            sa.text("SELECT theme_code AS code, name FROM themes ORDER BY theme_code")
        ).mappings()
        result = {row["code"]: (Theme.model_validate(row), []) for row in themes}
        members = conn.execute(sa.text("SELECT theme_code, stock_code FROM theme_corporations"))
        for theme_code, stock_code in members:
            result[theme_code][1].append(stock_code)
    return list(result.values())


def find_theme_stocks(engine: sa.Engine, theme_code: str) -> list[ThemeStock] | None:
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM themes WHERE theme_code = :code"), {"code": theme_code}
        ).first()
        if exists is None:
            return None
        rows = conn.execute(
            sa.text(
                "SELECT tc.stock_code AS code, c.name, tc.is_major"
                " FROM theme_corporations tc"
                " JOIN corporations c ON c.stock_code = tc.stock_code"
                " WHERE tc.theme_code = :code"
                " ORDER BY tc.stock_code"
            ),
            {"code": theme_code},
        ).mappings()
        return [ThemeStock.model_validate(row) for row in rows]


def find_stock_themes(
    engine: sa.Engine, stock_code: str
) -> list[tuple[StockTheme, list[str]]] | None:
    with engine.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM corporations WHERE stock_code = :code"), {"code": stock_code}
        ).first()
        if exists is None:
            return None
        rows = conn.execute(
            sa.text(
                "SELECT t.theme_code AS code, t.name, tc.is_major, m.stock_code AS member"
                " FROM theme_corporations tc"
                " JOIN themes t ON t.theme_code = tc.theme_code"
                " JOIN theme_corporations m ON m.theme_code = tc.theme_code"
                " WHERE tc.stock_code = :code"
                " ORDER BY t.theme_code, m.stock_code"
            ),
            {"code": stock_code},
        ).mappings()
        result: dict[str, tuple[StockTheme, list[str]]] = {}
        for row in rows:
            entry = result.setdefault(row["code"], (StockTheme.model_validate(row), []))
            entry[1].append(row["member"])
        return list(result.values())
