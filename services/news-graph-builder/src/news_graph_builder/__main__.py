import sys
from contextlib import ExitStack

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger, log_run, setup_logging

from news_graph_builder.cluster import find_cluster_articles, find_stale_clusters, lock_cluster
from news_graph_builder.graph import (
    extract,
    has_corporations,
    merge_company_entities,
    resolve,
    write_graph,
)
from news_graph_builder.settings import Settings

log = get_logger(__name__)
# Session-level advisory lock key that lets only one run build graphs at a time:
# https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
RUN_LOCK = int.from_bytes(b"ngrb")


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="news-graph-builder")
    failed = 0
    with log_run(log), httpx.Client() as client, ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
        run_lock = cleanup.enter_context(engine.connect())
        if not run_lock.execute(
            sa.text("SELECT pg_try_advisory_lock(:id)"), {"id": RUN_LOCK}
        ).scalar_one():
            log.info("run_skipped", reason="already_running")
            sys.exit(0)
        run_lock.commit()

        with engine.connect() as conn:
            if not has_corporations(conn):
                # Without corporations every company would become a plain entity for good.
                log.error("corporations_missing")
                sys.exit(1)

        with engine.begin() as conn:
            merged = merge_company_entities(conn)
        log.info("entities_merged", count=merged)

        with engine.connect() as conn:
            clusters = find_stale_clusters(conn)
        for cluster_id, seen in clusters:
            try:
                with engine.connect() as conn:
                    articles = find_cluster_articles(conn, cluster_id)
                if not articles:
                    continue
                extraction = extract(client, articles)
                with engine.begin() as conn:
                    if not lock_cluster(conn, cluster_id, seen):
                        log.info("cluster_changed", cluster_id=cluster_id)
                        continue
                    entity_ids = resolve(conn, extraction.entities)
                    dropped = write_graph(conn, cluster_id, seen, extraction, entity_ids)
            except Exception:
                log.exception("graph_extraction_failed", cluster_id=cluster_id)
                failed += 1
                continue
            if dropped:
                log.info("dangling_relations_dropped", cluster_id=cluster_id, count=dropped)
        log.info("graphs_built", count=len(clusters) - failed, failed=failed)
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
