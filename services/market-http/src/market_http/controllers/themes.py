from collections.abc import Callable
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Path, Query, Request

from market_http.controllers.dependencies import CODE
from market_http.repositories.themes import (
    StockTheme,
    Theme,
    ThemeStock,
    find_stock_themes,
    find_theme_stocks,
    list_themes,
)

router = APIRouter()

THEME_CODE = r"^[0-9A-Za-z]{1,20}$"
SNAPSHOT_NOTICE = (
    "Data is the current snapshot synced by market-syncer without membership history;"
    " do not expose it as point-in-time knowledge in historical evaluations."
)
ERRORS = {
    503: {"description": "Theme data store unavailable"},
}


def _read[T](request: Request, query: Callable[[sa.Engine], T]) -> T:
    engine: sa.Engine | None = request.app.state.postgres_engine
    if engine is None:
        raise HTTPException(503, "theme data unavailable")
    try:
        return query(engine)
    except sa.exc.SQLAlchemyError as error:
        raise HTTPException(503, "theme data unavailable") from error


@router.get(
    "/themes",
    description=f"Returns every theme ordered by `code` ascending. {SNAPSHOT_NOTICE}",
    responses=ERRORS,
)
def get_themes(request: Request) -> list[Theme]:
    return _read(request, list_themes)


@router.get(
    "/themes/{code}/stocks",
    description=(
        "Returns KOSPI stocks in the theme ordered by stock `code` ascending. Fewer than"
        f" `limit` results means there is no next page. {SNAPSHOT_NOTICE}"
    ),
    responses={404: {"description": "Theme not found"}, **ERRORS},
)
def get_theme_stocks(
    request: Request,
    code: Annotated[str, Path(pattern=THEME_CODE)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ThemeStock]:
    stocks = _read(request, lambda engine: find_theme_stocks(engine, code, limit, offset))
    if stocks is None:
        raise HTTPException(404, "theme not found")
    return stocks


@router.get(
    "/stocks/{code}/themes",
    description=(
        f"Returns themes containing the stock ordered by theme `code` ascending. {SNAPSHOT_NOTICE}"
    ),
    responses={404: {"description": "Stock not found"}, **ERRORS},
)
def get_stock_themes(
    request: Request, code: Annotated[str, Path(pattern=CODE)]
) -> list[StockTheme]:
    themes = _read(request, lambda engine: find_stock_themes(engine, code))
    if themes is None:
        raise HTTPException(404, "stock not found")
    return themes
