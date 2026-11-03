from collections.abc import Awaitable, Callable
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Path, Request

from market_http.controllers.dependencies import CODE, Valkey
from market_http.repositories.themes import StockTheme, Theme, ThemeGroup, ThemeStock
from market_http.services.themes import get_stock_themes, get_theme_stocks, get_themes

router = APIRouter()

THEME_CODE = r"^[0-9A-Za-z]{1,20}$"
ERRORS = {503: {"description": "Theme data store unavailable"}}


async def _read[T](request: Request, query: Callable[[sa.Engine], Awaitable[T]]) -> T:
    engine: sa.Engine | None = request.app.state.postgres_engine
    if engine is None:
        raise HTTPException(503, "theme data unavailable")
    try:
        return await query(engine)
    except sa.exc.SQLAlchemyError as error:
        raise HTTPException(503, "theme data unavailable") from error


@router.get(
    "/themes",
    responses=ERRORS,
)
async def list_all_themes(request: Request, valkey: Valkey) -> list[Theme | ThemeGroup]:
    return await _read(request, lambda engine: get_themes(engine, valkey))


@router.get(
    "/themes/{code}/stocks",
    responses={404: {"description": "Theme not found"}, **ERRORS},
)
async def list_theme_stocks(
    request: Request, valkey: Valkey, code: Annotated[str, Path(pattern=THEME_CODE)]
) -> list[ThemeStock]:
    stocks = await _read(request, lambda engine: get_theme_stocks(engine, valkey, code))
    if stocks is None:
        raise HTTPException(404, "theme not found")
    return stocks


@router.get(
    "/stocks/{code}/themes",
    responses={404: {"description": "Stock not found"}, **ERRORS},
)
async def list_stock_themes(
    request: Request, valkey: Valkey, code: Annotated[str, Path(pattern=CODE)]
) -> list[StockTheme]:
    themes = await _read(request, lambda engine: get_stock_themes(engine, valkey, code))
    if themes is None:
        raise HTTPException(404, "stock not found")
    return themes
