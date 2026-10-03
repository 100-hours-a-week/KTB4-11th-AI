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
        ) as run,
        ExitStack() as cleanup,
    ):
        stage = "engine_create"
        started = time.perf_counter()
        try:
            engine = sa.create_engine(settings.postgres_dsn)
            cleanup.callback(engine.dispose)

            stage = "embedding_load"
            with engine.connect() as conn:
                article_ids, vectors = load_embeddings(conn)

            loaded = time.perf_counter()
            log.info(
                "embedding_loaded",
                article_count=len(article_ids),
                load_seconds=loaded - started,
            )

            stage = "clustering"
            labels = dbscan(vectors, settings.eps, settings.min_samples)

            clustered = time.perf_counter()
            new: dict[int, set[int]] = {}
            for article_id, label in zip(article_ids, labels.tolist(), strict=True):
                if label != NOISE:
                    new.setdefault(label, set()).add(article_id)
            clustered_count = sum(len(members) for members in new.values())
            log.info(
                "clustering_completed",
                article_count=len(article_ids),
                cluster_count=len(new),
                noise_count=len(article_ids) - clustered_count,
                clustering_seconds=clustered - loaded,
            )
            # Keep the event and field names stable; they decide when to leave
            # full-recompute DBSCAN.
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
                stage = "assignment_load"
                old = load_assignment(conn)

                assigned = time.perf_counter()
                log.info(
                    "assignment_loaded",
                    existing_cluster_count=len(old),
                    existing_mapping_count=sum(len(members) for members in old.values()),
                    assignment_seconds=assigned - assignment_started,
                )

                stage = "cluster_matching"
                matches, unmatched = match(new, old)

                matched = time.perf_counter()
                log.info(
                    "clusters_matched",
                    new_cluster_count=len(new),
                    matched_cluster_count=sum(old_id is not None for old_id in matches.values()),
                    new_cluster_count_to_create=sum(old_id is None for old_id in matches.values()),
                    old_cluster_count_to_delete=len(unmatched),
                    matching_seconds=matched - assigned,
                )

                stage = "cluster_write"
                stats = write_clusters(conn, new, old, matches, unmatched)

                written = time.perf_counter()
            log.info(
                "cluster_write_result",
                **stats.__dict__,
                assignment_seconds=assigned - assignment_started,
                matching_seconds=matched - assigned,
                write_seconds=written - matched,
                total_seconds=written - started,
            )
        except Exception as error:
            run["stage"] = stage
            log.exception(
                "clusterer_run_failed",
                stage=stage,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise


if __name__ == "__main__":
    main()
