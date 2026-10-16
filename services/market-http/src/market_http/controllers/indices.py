from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from market_http.controllers.dependencies import Valkey

router = APIRouter()


class IndexQuote(BaseModel):
    name: Literal["KOSPI", "KOSPI200"]
    value: float
    change: float
    rate: float
    timestamp: datetime = Field(validation_alias=AliasChoices("timestamp", "ts"))

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "KOSPI",
                    "value": 2750.12,
                    "change": -12.34,
                    "rate": -0.45,
                    "timestamp": "2026-10-15T09:01:00+09:00",
                }
            ]
        }
    )


@router.get("/indices/{name}")
async def index_quote(name: Literal["KOSPI", "KOSPI200"], valkey: Valkey) -> IndexQuote:
    value = await valkey.get(f"index:{name}")
    if value is None:
        raise HTTPException(status_code=404, detail="index unavailable")
    return IndexQuote.model_validate_json(value)
