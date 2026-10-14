from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from ktb_core.krx import KST

from market_http.controllers.dependencies import Valkey
from market_http.repositories.rankings import RankingType, read_rankings

router = APIRouter()


@router.get("/rankings/{kind}")
async def rankings(
    kind: RankingType, valkey: Valkey, limit: Annotated[int, Query(ge=1, le=100)] = 100
) -> dict[str, object]:
    return await read_rankings(valkey, kind, limit, datetime.now(KST))
