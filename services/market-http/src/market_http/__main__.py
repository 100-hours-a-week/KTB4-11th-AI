import sqlalchemy as sa
import uvicorn
from ktb_core.logging import setup_logging
from ktb_telemetry.tracing import tracing
from opentelemetry.propagate import set_global_textmap

from market_http.app import create_app
from market_http.observability.metrics import SSEConnectionMetrics
from market_http.observability.tracing import LocalContextPropagator
from market_http.settings import Settings
from market_http.valkey import connect_valkey


def main() -> None:
    set_global_textmap(LocalContextPropagator())
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-http")
    app = create_app(
        connect_valkey(settings),
        settings.questdb_conf,
        postgres_engine=sa.create_engine(
            settings.postgres_dsn,
            pool_size=settings.postgres_pool_size,
            max_overflow=0,
            pool_timeout=settings.postgres_timeout_seconds,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": settings.postgres_timeout_seconds,
                "options": f"-c statement_timeout={settings.postgres_timeout_seconds * 1000}",
            },
        ),
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
        trace_spans_per_second=settings.trace_spans_per_second,
        prometheus_host=settings.prometheus_host,
        prometheus_port=settings.prometheus_port if settings.prometheus_enabled else None,
    )
    with tracing("market-http"):
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
