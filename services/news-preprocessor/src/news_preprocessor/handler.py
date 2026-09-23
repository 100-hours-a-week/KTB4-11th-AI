import logging
from typing import Any

import sqlalchemy as sa
from ktb_core.logging import setup_logging

from news_preprocessor.embed import embed
from news_preprocessor.embed_pending import embed_pending
from news_preprocessor.scrape import scrape
from news_preprocessor.settings import Settings
from news_preprocessor.sources.publishers import SOURCES

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, dict[str, Any]]:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-preprocessor started")
    engine = sa.create_engine(settings.postgres_dsn)
    try:
        scraped = [scrape(engine, source) for source in SOURCES]
        embedded = embed_pending(engine, embed, settings.embed_batch_limit)
    finally:
        engine.dispose()

    scraped_succeed = [article for result in scraped for article in result.succeed]
    scraped_failed = [article for result in scraped for article in result.failed]
    report = {
        "scraped": _report(scraped_succeed, scraped_failed),
        "embedded": _report(embedded.succeed, embedded.failed),
    }
    if scraped_failed or embedded.failed:
        raise RuntimeError(f"failed: {', '.join(scraped_failed + embedded.failed)}")
    return report


def _report(succeed: list[str], failed: list[str]) -> dict[str, Any]:
    return {
        "succeed": succeed,
        "succeed_count": len(succeed),
        "failed": failed,
        "failed_count": len(failed),
    }
