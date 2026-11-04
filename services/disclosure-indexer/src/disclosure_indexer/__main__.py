import argparse
from contextlib import ExitStack

import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging, start_logging

from disclosure_indexer.index import index_documents
from disclosure_indexer.settings import Settings

log = get_logger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retry-receipt", action="append", default=[])
    args = parser.parse_args(argv)
    settings = Settings()
    setup_logging(settings.log_level, service_name="disclosure-indexer")
    if not settings.enabled:
        if args.retry_receipt:
            raise ValueError("Enable disclosure indexing before retrying receipts")
        log.info("disclosure_index_skipped", reason="disabled")
        return
    with start_logging(log) as run, ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True)
        cleanup.callback(engine.dispose)
        indexed = index_documents(engine, settings, retry_receipts=tuple(args.retry_receipt))
        log.info("disclosure_index_complete", **indexed)
        run["indexed"] = indexed
        if indexed["failed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
