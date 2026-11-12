from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Query, Request
from redis.asyncio import Redis

from market_http.market_subscription import MarketSubscription

CODE = r"^[0-9A-Z]{6}$"


def _valkey(request: Request) -> Redis:
    return request.app.state.valkey


def _questdb_conf(request: Request) -> str:
    return request.app.state.questdb_conf


def _market_subscription(request: Request) -> MarketSubscription:
    return request.app.state.market_subscription


def _postgres_engine(request: Request) -> sa.Engine:
    engine: sa.Engine | None = request.app.state.postgres_engine
    if engine is None:
        raise HTTPException(503, "data store unavailable")
    return engine


def _codes(codes: Annotated[str, Query(pattern=r"^[0-9A-Z]{6}(,[0-9A-Z]{6})*$")]) -> list[str]:
    return list(dict.fromkeys(codes.split(",")))


Valkey = Annotated[Redis, Depends(_valkey)]
QuestDBConf = Annotated[str, Depends(_questdb_conf)]
PostgresEngine = Annotated[sa.Engine, Depends(_postgres_engine)]
Codes = Annotated[list[str], Depends(_codes)]
SharedMarketSubscription = Annotated[MarketSubscription, Depends(_market_subscription)]
