from functools import partial
from typing import Any

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger, log_run, setup_logging

from news_preprocessor.embed import embed
from news_preprocessor.embed_pending import embed_pending
from news_preprocessor.scrape import scrape
from news_preprocessor.settings import Settings
from news_preprocessor.sources.publishers import publishers

log = get_logger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, dict[str, Any]]:
    settings = Settings()
    setup_logging(settings.log_level, service_name="news-preprocessor")
    with log_run(log):
        engine = sa.create_engine(settings.postgres_dsn)
        client = httpx.Client(headers={"User-Agent": settings.user_agent}, follow_redirects=True)
        try:
            scraped = [scrape(engine, source) for source in publishers(client)]
            embedded = embed_pending(
                engine,
                partial(embed, api_key=settings.embedding_api_key),
                settings.embed_batch_limit,
            )
        finally:
            client.close()
            engine.dispose()

        scraped_succeed = [article for result in scraped for article in result.succeed]
        scraped_failed = [article for result in scraped for article in result.failed]
        report = {
            "scraped": {
                "succeed": scraped_succeed,
                "succeed_count": len(scraped_succeed),
                "failed": scraped_failed,
                "failed_count": len(scraped_failed),
            },
            "embedded": {
                "succeed": embedded.succeed,
                "succeed_count": len(embedded.succeed),
                "failed": embedded.failed,
                "failed_count": len(embedded.failed),
            },
        }
        if scraped_failed or embedded.failed:
            raise RuntimeError(f"failed: {', '.join(scraped_failed + embedded.failed)}")
        return report
