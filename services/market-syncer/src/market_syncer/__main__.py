import sys
from contextlib import ExitStack

import sqlalchemy as sa
from ktb_core.logging import get_logger, set_logger_level, setup_logging, start_logging

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

log = get_logger(__name__)
# Session-level advisory lock key that lets only one syncer run at a time:
# https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS
RUN_LOCK = int.from_bytes(b"mksy")
INDEX_NAME = "KOSPI200"


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-syncer")
    set_logger_level("urllib3", "INFO")
    interval = settings.kiwoom_request_interval
    failed = False
    with start_logging(log), ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn)
        cleanup.callback(engine.dispose)
        run_lock = cleanup.enter_context(engine.connect())
        if not run_lock.execute(
            sa.text("SELECT pg_try_advisory_lock(:id)"), {"id": RUN_LOCK}
        ).scalar_one():
            log.info("run_skipped", reason="already_running")
            sys.exit(0)
        run_lock.commit()

        client = build_client(settings)
        try:
            client.auth.get_access_token()
        except Exception:
            log.exception("kiwoom_token_failed")
            sys.exit(1)

        try:
            kospi = fetch_kospi(client, interval=interval)
            dart = fetch_corp_codes(settings.dart_api_key.get_secret_value())
            with engine.begin() as conn:
                joined = sync_corporations(conn, kospi, dart)
            log.info("corporations_synced", kiwoom=len(kospi), dart=len(dart), joined=joined)
        except Exception:
            log.exception("corporation_sync_failed")
            failed = True
            with engine.connect() as conn:
                if not has_corporations(conn):
                    sys.exit(1)

        try:
            codes = fetch_kospi200_codes(client, interval=interval)
            with engine.begin() as conn:
                kept, skipped = replace_index(conn, INDEX_NAME, codes)
            log.info("index_synced", index_name=INDEX_NAME, members=kept, skipped=skipped)
        except Exception:
            log.exception("index_sync_failed", index_name=INDEX_NAME)
            failed = True

        try:
            themes = fetch_themes(client, interval=interval)
            members = fetch_theme_members(
                client, theme_codes=[theme.code for theme in themes], interval=interval
            )
            with engine.begin() as conn:
                kept, main_stocks, skipped = sync_themes(conn, themes=themes, members=members)
            log.info(
                "themes_synced", themes=len(themes), members=kept, main=main_stocks, skipped=skipped
            )
        except Exception:
            log.exception("theme_sync_failed")
            failed = True
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
