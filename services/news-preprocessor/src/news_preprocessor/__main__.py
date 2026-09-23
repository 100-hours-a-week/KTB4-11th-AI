import logging
import sys

import sqlalchemy as sa
from ktb_core.logging import setup_logging

from news_preprocessor.embed import embed
from news_preprocessor.embed_pending import embed_pending
from news_preprocessor.scrape import scrape
from news_preprocessor.settings import Settings
from news_preprocessor.sources.publishers import SOURCES

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-preprocessor started")
    engine = sa.create_engine(settings.postgres_dsn)
    try:
        scraped = [scrape(engine, source) for source in SOURCES]
        embedded = embed_pending(engine, embed, settings.embed_batch_limit)
    finally:
        engine.dispose()
    failed = [result for result in scraped if result.failed] or embedded.failed
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
