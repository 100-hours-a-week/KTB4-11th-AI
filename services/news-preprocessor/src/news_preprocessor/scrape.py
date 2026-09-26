import logging
from typing import NamedTuple

import sqlalchemy as sa

from news_preprocessor.sources import NewsSource
from news_preprocessor.storage import insert_new, known_external_ids

logger = logging.getLogger(__name__)


class ScrapeResult(NamedTuple):
    succeed: list[str]
    failed: list[str]


def scrape(engine: sa.Engine, source: NewsSource) -> ScrapeResult:
    try:
        entries = source.entries()
    except Exception:
        logger.exception("feed failed for %s", source.source)
        return ScrapeResult(succeed=[], failed=[source.feed_url])

    with engine.connect() as conn:
        known = known_external_ids(conn, source.source, [entry.external_id for entry in entries])

    succeed: list[str] = []
    failed: list[str] = []
    for entry in entries:
        if entry.external_id in known:
            continue
        try:
            item = source.article(entry)
        except Exception:
            logger.exception("article failed for %s: %s", source.source, entry.url)
            failed.append(entry.external_id)
            continue
        with engine.begin() as conn:
            if insert_new(conn, item):
                succeed.append(entry.external_id)
    logger.info(
        "%s: %d feed entries, %d new articles, %d failed",
        source.source,
        len(entries),
        len(succeed),
        len(failed),
    )
    return ScrapeResult(succeed=succeed, failed=failed)
