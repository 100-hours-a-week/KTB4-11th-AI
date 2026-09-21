"""Entry point for the news-preprocessor."""

import logging

from ktb_core.logging import setup_logging

from news_preprocessor.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("news-preprocessor started")


if __name__ == "__main__":
    main()
