from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from market_http.controllers import bars, indices, prices, rankings


def create_app(valkey: Redis, questdb_conf: str) -> FastAPI:
    app = FastAPI(title="market-http")
    app.state.valkey = valkey
    app.state.questdb_conf = questdb_conf
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])
    app.include_router(prices.router)
    app.include_router(bars.router)
    app.include_router(indices.router)
    app.include_router(rankings.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
