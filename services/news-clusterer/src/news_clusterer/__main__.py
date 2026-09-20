"""Entry point for the news clusterer."""

import logging

import uvicorn
from ktb_core.logging import setup_logging

from news_clusterer.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info(
        "news-clusterer listening on %s:%s", settings.host, settings.port
    )
    uvicorn.run(
        "news_clusterer.app:app",
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
