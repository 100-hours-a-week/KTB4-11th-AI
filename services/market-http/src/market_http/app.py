from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from market_http.controllers import bars, prices


def create_app(
    valkey: Redis,
    questdb_conf: str,
    cors_origins: list[str] | None = None,
    cors_headers: list[str] | None = None,
) -> FastAPI:
    app = FastAPI(title="market-http")
    app.state.valkey = valkey
    app.state.questdb_conf = questdb_conf
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins if cors_origins is not None else [],
        allow_methods=["GET"],
        allow_headers=cors_headers if cors_headers is not None else [],
    )
    app.include_router(prices.router)
    app.include_router(bars.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
