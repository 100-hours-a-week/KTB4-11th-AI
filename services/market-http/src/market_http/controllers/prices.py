from fastapi import APIRouter

from market_http.controllers.dependencies import Codes, QuestDBConf, Valkey
from market_http.repositories.prices import Price, current_prices

router = APIRouter()


@router.get("/prices")
async def get_prices(valkey: Valkey, conf: QuestDBConf, codes: Codes) -> list[Price]:
    return await current_prices(valkey, conf, codes)
