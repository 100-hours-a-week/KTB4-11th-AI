from collections.abc import Awaitable
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Path

from market_http.controllers.dependencies import PostgresEngine, Valkey
from market_http.repositories.themes import Theme, ThemeGroup, ThemeStock
from market_http.services.themes import get_theme_stocks, get_themes

router = APIRouter()

THEME_CODE = r"^[0-9A-Za-z]{1,20}$"
ERRORS = {503: {"description": "Theme data store unavailable"}}


async def _read[T](query: Awaitable[T]) -> T:
    try:
        return await query
    except sa.exc.SQLAlchemyError as error:
        raise HTTPException(503, "theme data unavailable") from error


@router.get(
    "/themes",
    responses=ERRORS,
)
async def list_all_themes(engine: PostgresEngine, valkey: Valkey) -> list[Theme | ThemeGroup]:
    return await _read(get_themes(engine, valkey))


@router.get(
    "/themes/{code}/stocks",
    responses={404: {"description": "Theme not found"}, **ERRORS},
)
async def list_theme_stocks(
    engine: PostgresEngine, valkey: Valkey, code: Annotated[str, Path(pattern=THEME_CODE)]
) -> list[ThemeStock]:
    stocks = await _read(get_theme_stocks(engine, valkey, code))
    if stocks is None:
        raise HTTPException(404, "theme not found")
    return stocks
