import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from market_http.openapi_examples import (
    STOCK_THEME_EXAMPLE,
    THEME_EXAMPLE,
    THEME_GROUP_EXAMPLE,
    THEME_STOCK_EXAMPLE,
)


class Theme(BaseModel):
    code: str
    name: str

    model_config = ConfigDict(json_schema_extra={"examples": [THEME_EXAMPLE]})


class ThemeGroup(BaseModel):
    name: str
    items: list[Theme]

    model_config = ConfigDict(json_schema_extra={"examples": [THEME_GROUP_EXAMPLE]})


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
        rows = conn.execute(
            sa.text(
                "SELECT t.theme_code AS code, t.name, tc.stock_code AS member"
                " FROM themes t"
                " JOIN theme_corporations tc ON tc.theme_code = t.theme_code"
                " JOIN corporation_indices ci"
                " ON ci.stock_code = tc.stock_code AND ci.index_name = 'KOSPI200'"
                " ORDER BY t.theme_code, tc.stock_code"
            )
        ).mappings()
        result: dict[str, tuple[Theme, list[str]]] = {}
        for row in rows:
            entry = result.setdefault(row["code"], (Theme.model_validate(row), []))
            entry[1].append(row["member"])
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
                " JOIN corporation_indices ci"
                " ON ci.stock_code = tc.stock_code AND ci.index_name = 'KOSPI200'"
                " WHERE tc.theme_code = :code"
                " ORDER BY tc.stock_code"
            ),
            {"code": theme_code},
        ).mappings()
        return [ThemeStock.model_validate(row) for row in rows]
