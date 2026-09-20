"""Entry point for the news preprocessor.

Runs once and exits. Cron-shaped on purpose: no loop and no in-process
scheduler, because scheduling is the platform's job.
"""

import logging

from ktb_core.logging import setup_logging

from news_preprocessor.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("news-preprocessor started")


if __name__ == "__main__":
    main()
