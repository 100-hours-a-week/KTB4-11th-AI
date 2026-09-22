"""Entry point for the news-preprocessor: one scrape-then-embed pass, then exit."""

import logging
import sys
from collections.abc import Callable, Sequence
from functools import partial
from itertools import batched

import sqlalchemy as sa
from ktb_core.embedding import embed
from ktb_core.logging import setup_logging

from news_preprocessor.settings import Settings
from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers import SOURCES
from news_preprocessor.storage import (
    insert_new,
    known_external_ids,
    pending_embedding,
    set_embedding,
)

EMBED_BATCH_SIZE = 16

logger = logging.getLogger(__name__)

Embedder = Callable[[list[str]], list[list[float]]]


def run(
    engine: sa.Engine,
    sources: Sequence[NewsSource],
    embedder: Embedder,
    embed_batch_limit: int,
) -> bool:
    """Scrape every source, then embed pending articles. Returns False if anything failed."""
    ok = True
    for source in sources:
        if not _scrape(engine, source):
            ok = False
    if not _embed_pending(engine, embedder, embed_batch_limit):
        ok = False
    return ok


def _scrape(engine: sa.Engine, source: NewsSource) -> bool:
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


def _embed_pending(engine: sa.Engine, embedder: Embedder, limit: int) -> bool:
    with engine.connect() as conn:
        rows = pending_embedding(conn, limit)

    embedded = 0
    for batch in batched(rows, EMBED_BATCH_SIZE):
        try:
            vectors = embedder([f"{row.title}\n\n{row.body}" for row in batch])
        except Exception:
            logger.exception("embedding failed; %d articles stay pending", len(rows) - embedded)
            return False
        with engine.begin() as conn:
            set_embedding(conn, [row.id for row in batch], vectors)
        embedded += len(batch)
    logger.info("embedded %d articles", embedded)
    return True


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-preprocessor started")
    engine = sa.create_engine(settings.postgres_dsn)
    try:
        ok = run(
            engine,
            SOURCES,
            partial(embed, base_uri=settings.embedding_base_uri),
            settings.embed_batch_limit,
        )
    finally:
        engine.dispose()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
