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
                "SELECT t.theme_code AS code, t.name, tc.stock_code"
                " FROM themes t"
                " JOIN theme_companies tc ON tc.theme_code = t.theme_code"
                " JOIN corporation_indices ci ON ci.stock_code = tc.stock_code"
                " AND ci.index_name = 'KOSPI200'"
                " ORDER BY t.theme_code, tc.stock_code"
            )
        ).mappings()
        result: dict[str, tuple[Theme, list[str]]] = {}
        for row in rows:
            entry = result.setdefault(
                row["code"], (Theme(code=row["code"], name=row["name"]), [])
            )
            entry[1].append(row["stock_code"])
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
                " FROM theme_companies tc"
                " JOIN corporations c ON c.stock_code = tc.stock_code"
                " JOIN corporation_indices ci ON ci.stock_code = tc.stock_code"
                " AND ci.index_name = 'KOSPI200'"
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
                " FROM theme_companies tc"
                " JOIN themes t ON t.theme_code = tc.theme_code"
                " JOIN theme_companies m ON m.theme_code = tc.theme_code"
                " JOIN corporation_indices target ON target.stock_code = tc.stock_code"
                " AND target.index_name = 'KOSPI200'"
                " JOIN corporation_indices member_index ON member_index.stock_code = m.stock_code"
                " AND member_index.index_name = 'KOSPI200'"
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
