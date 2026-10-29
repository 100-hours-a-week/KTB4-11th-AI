from contextlib import ExitStack

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging, start_logging

from news_preprocessor.disclosures.collect import collect
from news_preprocessor.disclosures.settings import Settings

log = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(
        settings.log_level,
        service_name="news-preprocessor-disclosures",
        sensitive_query_params={"crtfc_key"},
    )
    with start_logging(log) as run, ExitStack() as cleanup:
        engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True)
        cleanup.callback(engine.dispose)
        client = cleanup.enter_context(httpx.Client())
        collected = collect(engine, client, settings)
        log.info("disclosure_collection_complete", **collected)
        run["collected"] = collected
        if collected["failed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
