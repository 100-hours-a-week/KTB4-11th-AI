import sqlalchemy as sa
import uvicorn
from disclosure_retrieval.settings import Settings as RetrievalSettings
from ktb_core.logging import setup_logging

from disclosure_http.app import create_app
from disclosure_http.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="disclosure-http")
    engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True)
    app = create_app(
        engine, RetrievalSettings(), settings.evaluation_clock_file, settings.public_url
    )
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
    finally:
        engine.dispose()
