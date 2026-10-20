from typing import Annotated

from fastapi import Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.engine import Engine

CODE = r"^[0-9A-Z]{6}$"


def _valkey(request: Request) -> Redis:
    return request.app.state.valkey


def _postgres_engine(request: Request) -> Engine:
    return request.app.state.postgres_engine


def _questdb_conf(request: Request) -> str:
    return request.app.state.questdb_conf


def _codes(codes: Annotated[str, Query(pattern=r"^[0-9A-Z]{6}(,[0-9A-Z]{6})*$")]) -> list[str]:
    return list(dict.fromkeys(codes.split(",")))


Valkey = Annotated[Redis, Depends(_valkey)]
QuestDBConf = Annotated[str, Depends(_questdb_conf)]
Codes = Annotated[list[str], Depends(_codes)]

PostgresEngine = Annotated[Engine, Depends(_postgres_engine)]
