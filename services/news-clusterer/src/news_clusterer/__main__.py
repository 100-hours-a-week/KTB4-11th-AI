import resource
import time
from contextlib import ExitStack

import sqlalchemy as sa
from ktb_core.embedding.config import EMBEDDING_DIMENSIONS
from ktb_core.logging import get_logger, setup_logging, start_logging

from news_clusterer.dbscan import NOISE, dbscan
from news_clusterer.match import match
from news_clusterer.settings import Settings
from news_clusterer.storage import load_assignment, load_embeddings, write_clusters

log = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="news-clusterer")
    with (
        start_logging(
            log,
            eps=settings.eps,
            min_samples=settings.min_samples,
            embedding_dimensions=EMBEDDING_DIMENSIONS,
        ),
        ExitStack() as cleanup,
    ):
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
        started = time.perf_counter()
        try:
            with engine.connect() as conn:
                article_ids, vectors = load_embeddings(conn)
        except Exception:
            log.exception("embedding_load_failed", stage="embedding_load")
            raise
        loaded = time.perf_counter()
        try:
            labels = dbscan(vectors, settings.eps, settings.min_samples)
        except Exception:
            log.exception("clustering_failed", stage="clustering")
            raise
        clustered = time.perf_counter()

        new: dict[int, set[int]] = {}
        for article_id, label in zip(article_ids, labels.tolist(), strict=True):
            if label != NOISE:
                new.setdefault(label, set()).add(article_id)
        clustered_count = sum(len(members) for members in new.values())
        # Keep the event and field names stable; they decide when to leave full-recompute DBSCAN.
        log.info(
            "clustering cost:",
            articles=len(article_ids),
            clusters=len(new),
            noise=len(article_ids) - clustered_count,
            load_seconds=loaded - started,
            dbscan_seconds=clustered - loaded,
            peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
        )

        with engine.begin() as conn:
            assignment_started = time.perf_counter()
            try:
                old = load_assignment(conn)
            except Exception:
                log.exception("assignment_load_failed", stage="assignment_load")
                raise
            assigned = time.perf_counter()
            try:
                matches, unmatched = match(new, old)
            except Exception:
                log.exception("cluster_matching_failed", stage="cluster_matching")
                raise
            matched = time.perf_counter()
            try:
                stats = write_clusters(conn, new, old, matches, unmatched)
            except Exception:
                log.exception("cluster_write_failed", stage="cluster_write")
                raise
            written = time.perf_counter()
        log.info(
            "cluster_write_result",
            **stats.__dict__,
            assignment_seconds=assigned - assignment_started,
            matching_seconds=matched - assigned,
            write_seconds=written - matched,
        )


if __name__ == "__main__":
    main()
