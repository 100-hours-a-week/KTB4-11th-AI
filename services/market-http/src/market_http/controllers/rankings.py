from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from ktb_core.krx import KST
from pydantic import BaseModel, ConfigDict

from market_http.controllers.dependencies import Valkey
from market_http.openapi_examples import RANKING_EXAMPLE
from market_http.repositories.rankings import RankingType, read_rankings

router = APIRouter()


class RankingItem(BaseModel):
    rank: int
    code: str
    score: float


class RankingResponse(BaseModel):
    as_of: date | None
    updated_at: datetime | None
    items: list[RankingItem]

    model_config = ConfigDict(json_schema_extra={"examples": [RANKING_EXAMPLE]})


@router.get("/rankings/{kind}")
async def rankings(
    kind: RankingType, valkey: Valkey, limit: Annotated[int, Query(ge=1, le=100)] = 100
) -> RankingResponse:
    return RankingResponse.model_validate(
        await read_rankings(valkey, kind, limit, datetime.now(KST))
    )
