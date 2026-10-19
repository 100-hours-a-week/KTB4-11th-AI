from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from ktb_core.krx import KST
from pydantic import BaseModel, ConfigDict

from market_http.controllers.dependencies import QuestDBConf, Valkey
from market_http.openapi_examples import RANKING_EXAMPLE
from market_http.repositories.rankings import RankingType
from market_http.services.rankings import get_rankings

router = APIRouter()


class RankingItem(BaseModel):
    rank: int
    code: str
    score: float
    stock_name: str | None
    sector_name: str | None
    price: int | None
    change_rate: float | None


class RankingResponse(BaseModel):
    as_of: date | None
    updated_at: datetime | None
    items: list[RankingItem]

    model_config = ConfigDict(json_schema_extra={"examples": [RANKING_EXAMPLE]})


@router.get("/rankings/{kind}")
async def rankings(
    kind: RankingType,
    valkey: Valkey,
    conf: QuestDBConf,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> RankingResponse:
    return RankingResponse.model_validate(
        await get_rankings(valkey, conf, kind, limit, datetime.now(KST))
    )
