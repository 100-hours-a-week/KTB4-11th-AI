from contextlib import ExitStack

import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging, start_logging

from disclosure_indexer.index import index_documents
from disclosure_indexer.settings import Settings

log = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="disclosure-indexer")
    with start_logging(log) as run, ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True)
        cleanup.callback(engine.dispose)
        indexed = index_documents(engine, settings)
        log.info("disclosure_index_complete", **indexed)
        run["indexed"] = indexed
        if indexed["failed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
