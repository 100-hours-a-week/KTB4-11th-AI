import logging
import sys
from contextlib import ExitStack

import httpx
import sqlalchemy as sa
from ktb_core.logging import setup_logging

from news_graph_builder.cluster import find_cluster_articles, find_stale_clusters, lock_cluster
from news_graph_builder.company import (
    fetch_corp_codes,
    fetch_kospi,
    has_companies,
    sync_companies,
)
from news_graph_builder.graph import extract, resolve, write_graph
from news_graph_builder.kiwoom import fetch_token
from news_graph_builder.settings import Settings
from news_graph_builder.theme import (
    fetch_kospi200_codes,
    fetch_theme_members,
    fetch_themes,
    sync_themes,
)

logger = logging.getLogger(__name__)
# Session-level advisory lock key that lets only one run build graphs at a time:
# https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
RUN_LOCK = int.from_bytes(b"ngrb")


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
        run_lock = cleanup.enter_context(engine.connect())
        if not run_lock.execute(
            sa.text("SELECT pg_try_advisory_lock(:id)"), {"id": RUN_LOCK}
        ).scalar_one():
            logger.info("another news-graph-builder run is in progress, exiting")
            sys.exit(0)
        run_lock.commit()
        token = None
        try:
            token = fetch_token(client)
        except Exception:
            logger.exception("Kiwoom token request failed")
            sync_failed = True

        if token is not None:
            try:
                kospi = fetch_kospi(client, token=token)
                dart = fetch_corp_codes()
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

        if sync_failed:
            with engine.connect() as conn:
                if not has_companies(conn):
                    # Without companies every company would become a plain entity for good.
                    sys.exit(1)

        if token is not None:
            try:
                themes = fetch_themes(client, token=token)
                kospi200_codes = fetch_kospi200_codes(client, token=token)
                theme_codes = [theme.code for theme in themes]
                members = fetch_theme_members(client, token=token, theme_codes=theme_codes)
                with engine.begin() as conn:
                    kept, main_stocks, skipped = sync_themes(
                        conn, themes=themes, kospi200_codes=kospi200_codes, members=members
                    )
                logger.info(
                    "synced themes: themes=%d kospi200=%d members=%d main=%d skipped=%d",
                    len(themes),
                    len(kospi200_codes),
                    kept,
                    main_stocks,
                    skipped,
                )
            except Exception:
                logger.exception("theme sync failed")
                sync_failed = True

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
                        logger.info("cluster %d changed during extraction, skipped", cluster_id)
                        continue
                    entity_ids = resolve(conn, extraction.entities)
                    dropped = write_graph(conn, cluster_id, seen, extraction, entity_ids)
            except Exception:
                logger.exception("graph extraction failed for cluster %d", cluster_id)
                failed += 1
                continue
            if dropped:
                logger.info("cluster %d: dropped %d dangling relations", cluster_id, dropped)
        logger.info("built %d cluster graphs, %d failed", len(clusters) - failed, failed)
    sys.exit(1 if sync_failed or failed else 0)


if __name__ == "__main__":
    main()
