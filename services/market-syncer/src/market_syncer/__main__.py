import logging
import sys
from contextlib import ExitStack

import sqlalchemy as sa
from ktb_core.logging import setup_logging

from market_syncer.corporations import has_corporations, replace_index, sync_corporations
from market_syncer.dart import fetch_corp_codes
from market_syncer.kiwoom import (
    build_client,
    fetch_kospi,
    fetch_kospi200_codes,
    fetch_theme_members,
    fetch_themes,
)
from market_syncer.settings import Settings
from market_syncer.themes import sync_themes

logger = logging.getLogger(__name__)
# Session-level advisory lock key that lets only one syncer run at a time:
# https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
RUN_LOCK = int.from_bytes(b"mksy")
INDEX_NAME = "KOSPI200"


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logger.info("market-syncer started")
    interval = settings.kiwoom_request_interval
    failed = False
    with ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
        run_lock = cleanup.enter_context(engine.connect())
        if not run_lock.execute(
            sa.text("SELECT pg_try_advisory_lock(:id)"), {"id": RUN_LOCK}
        ).scalar_one():
            logger.info("another market-syncer run is in progress, exiting")
            sys.exit(0)
        run_lock.commit()

        client = build_client(settings)
        try:
            client.auth.get_access_token()
        except Exception:
            logger.exception("Kiwoom token request failed")
            sys.exit(1)

        try:
            kospi = fetch_kospi(client, interval=interval)
            dart = fetch_corp_codes(settings.dart_api_key.get_secret_value())
            with engine.begin() as conn:
                joined = sync_corporations(conn, kospi, dart)
            logger.info(
                "synced corporations: kiwoom=%d dart=%d joined=%d", len(kospi), len(dart), joined
            )
        except Exception:
            logger.exception("corporation sync failed")
            failed = True
            with engine.connect() as conn:
                if not has_corporations(conn):
                    sys.exit(1)

        try:
            codes = fetch_kospi200_codes(client, interval=interval)
            with engine.begin() as conn:
                kept, skipped = replace_index(conn, INDEX_NAME, codes)
            logger.info("synced %s: members=%d skipped=%d", INDEX_NAME, kept, skipped)
        except Exception:
            logger.exception("%s sync failed", INDEX_NAME)
            failed = True

        try:
            themes = fetch_themes(client, interval=interval)
            members = fetch_theme_members(
                client, theme_codes=[theme.code for theme in themes], interval=interval
            )
            with engine.begin() as conn:
                kept, main_stocks, skipped = sync_themes(conn, themes=themes, members=members)
            logger.info(
                "synced themes: themes=%d members=%d main=%d skipped=%d",
                len(themes),
                kept,
                main_stocks,
                skipped,
            )
        except Exception:
            logger.exception("theme sync failed")
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
