import logging
from collections.abc import Callable
from itertools import batched
from typing import NamedTuple

import sqlalchemy as sa

from news_preprocessor.storage import pending_embedding, set_embedding

EMBED_BATCH_SIZE = 16

logger = logging.getLogger(__name__)


class EmbedResult(NamedTuple):
    succeed: list[str]
    failed: list[str]


def embed_pending(
    engine: sa.Engine, embedder: Callable[[list[str]], list[list[float]]], limit: int
) -> EmbedResult:
    with engine.connect() as conn:
        rows = pending_embedding(conn, limit)

    succeed: list[str] = []
    for batch in batched(rows, EMBED_BATCH_SIZE):
        try:
            vectors = embedder([f"{row.title}\n\n{row.body}" for row in batch])
        except Exception:
            pending = [row.external_id for row in rows[len(succeed) :]]
            logger.exception("embedding failed; %d articles stay pending", len(pending))
            return EmbedResult(succeed=succeed, failed=pending)
        with engine.begin() as conn:
            set_embedding(conn, [row.id for row in batch], vectors)
        succeed.extend(row.external_id for row in batch)
    logger.info("embedded %d articles", len(succeed))
    return EmbedResult(succeed=succeed, failed=[])
