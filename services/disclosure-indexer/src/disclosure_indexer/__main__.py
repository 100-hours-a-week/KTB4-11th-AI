from contextlib import ExitStack

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging, start_logging

from disclosure_indexer.collect import collect
from disclosure_indexer.index import index_documents
from disclosure_indexer.settings import Settings

log = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(
        settings.log_level, service_name="disclosure-indexer", sensitive_query_params={"crtfc_key"}
    )
    with start_logging(log) as run, ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True)
        cleanup.callback(engine.dispose)

        # Indexing runs even when collection fails, so eligible retries still make progress.
        try:
            with httpx.Client() as client:
                collected = collect(engine, client, settings)
        except Exception as error:
            collected = {"ready": 0, "failed": 1, "skipped": 0, "unavailable": 0}
            run["stage"] = "collection"
            log.exception("disclosure_collection_failed", error_type=type(error).__name__)
        log.info("disclosure_collection_complete", **collected)
        run["collected"] = collected

        indexed = index_documents(engine, settings)
        log.info("disclosure_index_complete", **indexed)
        run["indexed"] = indexed

        if collected["failed"] or indexed["failed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
