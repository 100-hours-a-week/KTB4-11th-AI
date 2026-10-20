import sqlalchemy as sa
import uvicorn
from ktb_core.logging import setup_logging

from market_http.app import create_app
from market_http.metrics import SSEConnectionMetrics
from market_http.settings import Settings
from market_http.valkey import connect_valkey


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-http")
    app = create_app(
        connect_valkey(settings),
        settings.questdb_conf,
        postgres_engine=sa.create_engine(settings.postgres_dsn, pool_pre_ping=True),
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
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
