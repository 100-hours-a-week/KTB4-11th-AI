import sys
from functools import partial

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging, start_logging

from news_preprocessor.embed import embed
from news_preprocessor.embed_pending import embed_pending
from news_preprocessor.scrape import scrape
from news_preprocessor.settings import Settings
from news_preprocessor.sources.publishers import publishers
from news_preprocessor.storage import kospi200_stock_codes

log = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(
        settings.log_level,
        service_name="news-preprocessor",
        sensitive_query_params={"crtfc_key"},
    )
    with start_logging(log):
        engine = sa.create_engine(settings.postgres_dsn)
        client = httpx.Client(headers={"User-Agent": settings.user_agent}, follow_redirects=True)
        try:
            with engine.connect() as conn:
                stock_codes = kospi200_stock_codes(conn)
            dart_api_key = settings.dart_api_key.get_secret_value()
            sources = publishers(client, dart_api_key, stock_codes)
            scraped = [scrape(engine, source) for source in sources]
            embedded = embed_pending(
                engine,
                partial(embed, api_key=settings.embedding_api_key),
                settings.embed_batch_limit,
            )
        finally:
            client.close()
            engine.dispose()
        failed = [result for result in scraped if result.failed] or embedded.failed
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
