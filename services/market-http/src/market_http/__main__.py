import uvicorn
import sqlalchemy as sa
from ktb_core.logging import setup_logging

from market_http.app import create_app
from market_http.metrics import SSEConnectionMetrics
from market_http.settings import Settings
from market_http.valkey import connect_valkey


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-http")
    engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True, pool_size=2, max_overflow=2)
    app = create_app(
        connect_valkey(settings),
        settings.questdb_conf,
        postgres_engine=engine,
        docs_enabled=settings.docs_enabled,
        docs_path_prefix=settings.docs_path_prefix,
        cors_origins=settings.cors_origins,
        cors_headers=settings.cors_headers,
        sse_metrics=(
            SSEConnectionMetrics(
                namespace=settings.metrics_namespace,
                cluster_name=settings.metrics_cluster_name,
                service_name=settings.metrics_service_name,
            )
            if settings.metrics_namespace
            else None
        ),
    )
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
    finally:
        engine.dispose()
