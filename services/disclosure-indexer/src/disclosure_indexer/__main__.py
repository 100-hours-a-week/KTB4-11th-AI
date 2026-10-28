import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging

from disclosure_indexer.collect import collect
from disclosure_indexer.index import index_documents
from disclosure_indexer.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(
        settings.log_level, service_name="disclosure-indexer", sensitive_query_params={"crtfc_key"}
    )
    engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True)
    try:
        try:
            with httpx.Client() as client:
                collected = collect(engine, client, settings)
        except Exception as error:
            collected = {"failed": 1}
            get_logger(__name__).warning(
                "disclosure_collection_failed", error_type=type(error).__name__
            )
        get_logger(__name__).info("disclosure_collection_complete", **collected)
        result = index_documents(engine, settings)
        get_logger(__name__).info("disclosure_index_complete", **result)
        if result["failed"] or collected["failed"]:
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
