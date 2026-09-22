import logging
from collections.abc import Callable
from itertools import batched

import sqlalchemy as sa

from news_preprocessor.storage import pending_embedding, set_embedding

EMBED_BATCH_SIZE = 16

logger = logging.getLogger(__name__)


def embed_pending(
    engine: sa.Engine, embedder: Callable[[list[str]], list[list[float]]], limit: int
) -> bool:
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
