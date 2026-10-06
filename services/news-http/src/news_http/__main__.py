import sqlalchemy as sa
import uvicorn
from ktb_core.logging import setup_logging

from news_http.app import create_app
from news_http.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="news-http")
    engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True)
    try:
        uvicorn.run(create_app(engine), host=settings.host, port=settings.port, log_config=None)
    finally:
        engine.dispose()
