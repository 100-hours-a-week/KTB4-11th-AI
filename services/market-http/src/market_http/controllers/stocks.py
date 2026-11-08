import asyncio
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Path, Query

from market_http.controllers.dependencies import CODE, PostgresEngine
from market_http.repositories.stocks import StockDetail, StockMatch, find_stock
from market_http.services.stocks import search_stocks

router = APIRouter()


@router.get("/stocks", responses={503: {"description": "Stock data store unavailable"}})
async def search(
    engine: PostgresEngine,
    q: Annotated[str, Query(pattern=r"\S", max_length=100)],
    size: Annotated[int | None, Query(ge=1, le=200)] = None,
) -> list[StockMatch]:
    try:
        return await asyncio.to_thread(search_stocks, engine, q.strip(), size)
    except sa.exc.SQLAlchemyError as error:
        raise HTTPException(503, "stock data unavailable") from error


@router.get(
    "/stocks/{code}",
    responses={
        404: {"description": "Stock not found"},
        503: {"description": "Stock data store unavailable"},
    },
)
async def get_stock(
    engine: PostgresEngine, code: Annotated[str, Path(pattern=CODE)]
) -> StockDetail:
    try:
        stock = await asyncio.to_thread(find_stock, engine, code)
    except sa.exc.SQLAlchemyError as error:
        raise HTTPException(503, "stock data unavailable") from error
    if stock is None:
        raise HTTPException(404, "stock not found")
    return stock
