from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from ktb_core.krx import KST
from pydantic import BaseModel, ConfigDict

from market_http.controllers.dependencies import Valkey
from market_http.repositories.rankings import RankingType, read_rankings

router = APIRouter()


class RankingItem(BaseModel):
    rank: int
    code: str
    score: float

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"rank": 1, "code": "005930", "score": 150000.0}]}
    )


class RankingResponse(BaseModel):
    as_of: date | None
    updated_at: datetime | None
    items: list[RankingItem]

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "as_of": "2026-10-15",
                    "updated_at": "2026-10-15T09:01:00+09:00",
                    "items": [{"rank": 1, "code": "005930", "score": 150000.0}],
                }
            ]
        }
    )


@router.get("/rankings/{kind}")
async def rankings(
    kind: RankingType, valkey: Valkey, limit: Annotated[int, Query(ge=1, le=100)] = 100
) -> RankingResponse:
    return RankingResponse.model_validate(await read_rankings(valkey, kind, limit, datetime.now(KST)))
