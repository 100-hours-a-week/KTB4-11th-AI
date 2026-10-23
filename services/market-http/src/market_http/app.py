import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Literal

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from scalar_fastapi import get_scalar_api_reference

from market_http.controllers import bars, indices, orderbooks, prices, rankings
from market_http.observability.metrics import SSEConnectionMetrics
from market_http.observability.tracing import DeliverSpans, SSESendSpans, is_stream
from market_http.openapi_examples import HEALTH_EXAMPLE


class HealthResponse(BaseModel):
    status: Literal["ok"]

    model_config = ConfigDict(json_schema_extra={"examples": [HEALTH_EXAMPLE]})


def create_app(
    valkey: Redis,
    questdb_conf: str,
    *,
    postgres_engine: sa.Engine | None = None,
    docs_enabled: bool = True,
    docs_path_prefix: str = "",
    cors_origins: list[str] | None = None,
    cors_headers: list[str] | None = None,
    sse_metrics: SSEConnectionMetrics | None = None,
    trace_spans_per_second: int = 100,
) -> FastAPI:
    deliver_spans = DeliverSpans(trace_spans_per_second)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        metrics: SSEConnectionMetrics | None = app.state.sse_metrics
        publisher = (
            asyncio.create_task(metrics.publish(deliver_spans.take_counts))
            if metrics is not None
            else None
        )
        try:
            yield
        finally:
            if publisher is not None:
                publisher.cancel()
                with suppress(asyncio.CancelledError):
                    await publisher
            if postgres_engine is not None:
                await asyncio.to_thread(postgres_engine.dispose)

    app = FastAPI(
        title="market-http",
        lifespan=lifespan,
        telemetry={"auto_configure": False, "exclude": is_stream},
        root_path=docs_path_prefix,
        openapi_url="/openapi.json" if docs_enabled else None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.valkey = valkey
    app.state.questdb_conf = questdb_conf
    app.state.postgres_engine = postgres_engine
    app.state.sse_metrics = sse_metrics
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins if cors_origins is not None else [],
        allow_methods=["GET"],
        allow_headers=cors_headers if cors_headers is not None else [],
    )
    app.add_middleware(SSESendSpans, deliver_spans=deliver_spans)
    app.include_router(prices.router)
    app.include_router(orderbooks.router)
    app.include_router(bars.router)
    app.include_router(indices.router)
    app.include_router(rankings.router)

    if docs_enabled:

        @app.get("/scalar", include_in_schema=False)
        def scalar_reference():
            return get_scalar_api_reference(openapi_url=f"{docs_path_prefix}{app.openapi_url}")

    @app.get("/health")
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return app
