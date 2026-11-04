import asyncio
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Path, Request

from market_http.controllers.dependencies import CODE
from market_http.repositories.stocks import StockDetail, find_stock

router = APIRouter()


@router.get(
    "/stocks/{code}",
    responses={
        404: {"description": "Stock not found"},
        503: {"description": "Stock data store unavailable"},
    },
)
async def get_stock(request: Request, code: Annotated[str, Path(pattern=CODE)]) -> StockDetail:
    engine: sa.Engine | None = request.app.state.postgres_engine
    if engine is None:
        raise HTTPException(503, "stock data unavailable")
    try:
        stock = await asyncio.to_thread(find_stock, engine, code)
    except sa.exc.SQLAlchemyError as error:
        raise HTTPException(503, "stock data unavailable") from error
    if stock is None:
        raise HTTPException(404, "stock not found")
    return stock
