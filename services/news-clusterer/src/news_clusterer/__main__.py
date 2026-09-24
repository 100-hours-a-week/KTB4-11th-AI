import logging
import resource
import sys
import time

import httpx
import sqlalchemy as sa
from ktb_core.logging import setup_logging

from news_clusterer.dbscan import NOISE, dbscan
from news_clusterer.match import match
from news_clusterer.settings import Settings
from news_clusterer.storage import (
    cluster_articles,
    clusters_needing_summary,
    load_assignment,
    load_embeddings,
    set_summary,
    write_clusters,
)
from news_clusterer.summarize import summarize

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-clusterer started")
    engine = sa.create_engine(settings.postgres_dsn)
    client = httpx.Client()
    failed = 0
    try:
        started = time.perf_counter()
        with engine.connect() as conn:
            article_ids, vectors = load_embeddings(conn)
        loaded = time.perf_counter()
        labels = dbscan(vectors, settings.eps, settings.min_samples)
        clustered = time.perf_counter()

        new: dict[int, set[int]] = {}
        for article_id, label in zip(article_ids, labels.tolist(), strict=True):
            if label != NOISE:
                new.setdefault(label, set()).add(article_id)
        clustered_count = sum(len(members) for members in new.values())
        # Keep this key=value format stable: it decides when to leave full-recompute DBSCAN.
        logger.info(
            "clustering cost: articles=%d clusters=%d noise=%d load_seconds=%.2f"
            " dbscan_seconds=%.2f peak_rss_mib=%d",
            len(article_ids),
            len(new),
            len(article_ids) - clustered_count,
            loaded - started,
            clustered - loaded,
            # Linux reports ru_maxrss in KiB.
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        )

        with engine.begin() as conn:
            old = load_assignment(conn)
            matches, unmatched = match(new, old)
            write_clusters(conn, new, old, matches, unmatched)

        with engine.connect() as conn:
            pending = clusters_needing_summary(conn)
        for cluster_id in pending:
            try:
                with engine.connect() as conn:
                    articles = cluster_articles(conn, cluster_id)
                title, summary = summarize(
                    client,
                    articles,
                    base_uri=settings.llm_base_uri,
                    model=settings.llm_model,
                    max_chars=settings.summary_max_chars,
                    timeout=settings.llm_timeout,
                )
            except Exception:
                logger.exception("summary failed for cluster %d", cluster_id)
                failed += 1
                continue
            with engine.begin() as conn:
                set_summary(conn, cluster_id, title, summary)
        logger.info("summarized %d clusters, %d failed", len(pending) - failed, failed)
    finally:
        client.close()
        engine.dispose()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
