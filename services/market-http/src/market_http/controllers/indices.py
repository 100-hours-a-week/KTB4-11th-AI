from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from market_http.controllers.dependencies import Valkey

router = APIRouter()


class IndexQuote(BaseModel):
    name: Literal["KOSPI", "KOSPI200"]
    value: float
    change: float
    rate: float
    ts: datetime


@router.get("/indices/{name}")
async def index_quote(name: Literal["KOSPI", "KOSPI200"], valkey: Valkey) -> IndexQuote:
    value = await valkey.get(f"index:{name}")
    if value is None:
        raise HTTPException(status_code=404, detail="index unavailable")
    return IndexQuote.model_validate_json(value)
