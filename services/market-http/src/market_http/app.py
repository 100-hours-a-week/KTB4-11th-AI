from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from market_http.controllers import bars, prices

PREFIX = "/market"


def create_app(valkey: Redis, questdb_conf: str) -> FastAPI:
    app = FastAPI(
        title="market-http",
        docs_url=f"{PREFIX}/docs",
        redoc_url=f"{PREFIX}/redoc",
        openapi_url=f"{PREFIX}/openapi.json",
    )
    app.state.valkey = valkey
    app.state.questdb_conf = questdb_conf
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])

    market = APIRouter(prefix=PREFIX)
    market.include_router(prices.router)
    market.include_router(bars.router)

    @market.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(market)
    return app
