import logging
import sys
from contextlib import ExitStack

import httpx
import sqlalchemy as sa
from ktb_core.logging import setup_logging

from news_graph_builder.dart import fetch_corp_codes
from news_graph_builder.extract import extract
from news_graph_builder.kiwoom import fetch_kospi
from news_graph_builder.resolve import resolve
from news_graph_builder.settings import Settings
from news_graph_builder.storage import (
    cluster_articles,
    due_clusters,
    has_companies,
    lock_cluster,
    write_graph,
)
from news_graph_builder.sync_companies import sync_companies

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logger.info("news-graph-builder started")
    sync_failed = False
    failed = 0
    with httpx.Client() as client, ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
        try:
            kospi = fetch_kospi(
                client,
                base_uri=settings.kiwoom_base_uri,
                app_key=settings.kiwoom_app_key.get_secret_value(),
                secret_key=settings.kiwoom_secret_key.get_secret_value(),
            )
            dart = fetch_corp_codes(settings.dart_api_key.get_secret_value())
            with engine.begin() as conn:
                joined, merged = sync_companies(conn, kospi, dart)
            logger.info(
                "synced companies: kiwoom=%d dart=%d joined=%d merged=%d",
                len(kospi),
                len(dart),
                joined,
                merged,
            )
        except Exception:
            logger.exception("company sync failed")
            sync_failed = True
            with engine.connect() as conn:
                if not has_companies(conn):
                    # Without companies every company would become a plain entity for good.
                    sys.exit(1)

        with engine.connect() as conn:
            due = due_clusters(conn)
        for cluster_id, seen in due:
            try:
                with engine.connect() as conn:
                    articles = cluster_articles(conn, cluster_id)
                if not articles:
                    continue
                extraction = extract(
                    client,
                    articles,
                    base_uri=settings.llm_base_uri,
                    model=settings.llm_model,
                    max_chars=settings.summary_max_chars,
                    timeout=settings.llm_timeout,
                    max_entities=settings.max_entities,
                    max_relations=settings.max_relations,
                )
                with engine.begin() as conn:
                    if not lock_cluster(conn, cluster_id, seen):
                        logger.info("cluster %d changed during extraction, skipped", cluster_id)
                        continue
                    dropped = write_graph(
                        conn, cluster_id, seen, extraction, resolve(conn, extraction.entities)
                    )
            except Exception:
                logger.exception("graph extraction failed for cluster %d", cluster_id)
                failed += 1
                continue
            if dropped:
                logger.info("cluster %d: dropped %d dangling relations", cluster_id, dropped)
        logger.info("built %d cluster graphs, %d failed", len(due) - failed, failed)
    sys.exit(1 if sync_failed or failed else 0)


if __name__ == "__main__":
    main()
