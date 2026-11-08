import asyncio
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Path, Query, Request

from market_http.controllers.dependencies import CODE
from market_http.repositories.stocks import StockDetail, StockMatch, find_stock
from market_http.services.stocks import search_stocks

router = APIRouter()


def _engine(request: Request) -> sa.Engine:
    engine: sa.Engine | None = request.app.state.postgres_engine
    if engine is None:
        raise HTTPException(503, "stock data unavailable")
    return engine


@router.get("/stocks/search", responses={503: {"description": "Stock data store unavailable"}})
async def search(
    request: Request, q: Annotated[str, Query(pattern=r"\S", max_length=100)]
) -> list[StockMatch]:
    try:
        return await asyncio.to_thread(search_stocks, _engine(request), q.strip())
    except sa.exc.SQLAlchemyError as error:
        raise HTTPException(503, "stock data unavailable") from error


@router.get(
    "/stocks/{code}",
    responses={
        404: {"description": "Stock not found"},
        503: {"description": "Stock data store unavailable"},
    },
)
async def get_stock(request: Request, code: Annotated[str, Path(pattern=CODE)]) -> StockDetail:
    engine = _engine(request)
    try:
        stock = await asyncio.to_thread(find_stock, engine, code)
    except sa.exc.SQLAlchemyError as error:
        raise HTTPException(503, "stock data unavailable") from error
    if stock is None:
        raise HTTPException(404, "stock not found")
    return stock
