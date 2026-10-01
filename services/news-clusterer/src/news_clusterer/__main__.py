import resource
import time
from contextlib import ExitStack

import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging

from news_clusterer.dbscan import NOISE, dbscan
from news_clusterer.match import match
from news_clusterer.settings import Settings
from news_clusterer.storage import load_assignment, load_embeddings, write_clusters

log = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="news-clusterer")
    log.info("run_start")
    with ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
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
            old = load_assignment(conn)
            matches, unmatched = match(new, old)
            write_clusters(conn, new, old, matches, unmatched)


if __name__ == "__main__":
    main()
