from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from market_http.controllers import bars, indices, orderbooks, prices, rankings


def create_app(
    valkey: Redis, questdb_conf: str, docs_enabled: bool = True, docs_path_prefix: str = ""
) -> FastAPI:
    app = FastAPI(
        title="market-http",
        root_path=docs_path_prefix,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.valkey = valkey
    app.state.questdb_conf = questdb_conf
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])
    app.include_router(prices.router)
    app.include_router(orderbooks.router)
    app.include_router(bars.router)
    app.include_router(indices.router)
    app.include_router(rankings.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
