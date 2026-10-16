from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis

from market_http.controllers import bars, indices, orderbooks, prices, rankings


class HealthResponse(BaseModel):
    status: Literal["ok"]

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})


def create_app(
    valkey: Redis,
    questdb_conf: str,
    *,
    docs_enabled: bool = True,
    docs_path_prefix: str = "",
    cors_origins: list[str] | None = None,
    cors_headers: list[str] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="market-http",
        root_path=docs_path_prefix,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.valkey = valkey
    app.state.questdb_conf = questdb_conf
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins if cors_origins is not None else [],
        allow_methods=["GET"],
        allow_headers=cors_headers if cors_headers is not None else [],
    )
    app.include_router(prices.router)
    app.include_router(orderbooks.router)
    app.include_router(bars.router)
    app.include_router(indices.router)
    app.include_router(rankings.router)

    @app.get("/health")
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return app
