import logging

import sqlalchemy as sa

from news_preprocessor.sources import NewsSource
from news_preprocessor.storage import insert_new, known_external_ids

logger = logging.getLogger(__name__)


def scrape(engine: sa.Engine, source: NewsSource) -> bool:
    try:
        entries = source.entries()
    except Exception:
        logger.exception("feed failed for %s", source.source)
        return False

    with engine.connect() as conn:
        known = known_external_ids(conn, source.source, [entry.external_id for entry in entries])

    ok = True
    added = 0
    for entry in entries:
        if entry.external_id in known:
            continue
        try:
            item = source.article(entry)
        except Exception:
            logger.exception("article failed for %s: %s", source.source, entry.url)
            ok = False
            continue
        with engine.begin() as conn:
            added += insert_new(conn, item)
    logger.info("%s: %d feed entries, %d new articles", source.source, len(entries), added)
    return ok
