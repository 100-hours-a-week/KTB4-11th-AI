from typing import Annotated

from fastapi import Depends, Query, Request
from redis.asyncio import Redis

from market_http.tracing import SlidingWindowLimiter

CODE = r"^[0-9A-Z]{6}$"


def _valkey(request: Request) -> Redis:
    return request.app.state.valkey


def _questdb_conf(request: Request) -> str:
    return request.app.state.questdb_conf


def _span_limiter(request: Request) -> SlidingWindowLimiter:
    return request.app.state.span_limiter


def _codes(codes: Annotated[str, Query(pattern=r"^[0-9A-Z]{6}(,[0-9A-Z]{6})*$")]) -> list[str]:
    return list(dict.fromkeys(codes.split(",")))


Valkey = Annotated[Redis, Depends(_valkey)]
QuestDBConf = Annotated[str, Depends(_questdb_conf)]
Codes = Annotated[list[str], Depends(_codes)]
SpanLimiter = Annotated[SlidingWindowLimiter, Depends(_span_limiter)]
