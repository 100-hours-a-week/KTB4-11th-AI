import sqlalchemy as sa
import uvicorn
from ktb_core.logging import setup_logging

from disclosure_retrieval.app import create_app
from disclosure_retrieval.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="disclosure-retrieval")
    engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True)
    try:
        uvicorn.run(
            create_app(engine, settings), host=settings.host, port=settings.port, log_config=None
        )
    finally:
        engine.dispose()
