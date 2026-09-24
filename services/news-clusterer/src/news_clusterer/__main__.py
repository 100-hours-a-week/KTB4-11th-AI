import logging

from ktb_core.logging import setup_logging

from news_clusterer.settings import Settings

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logger.info("news-clusterer started")


if __name__ == "__main__":
    main()
