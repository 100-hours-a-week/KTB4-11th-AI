"""Entry point for the portfolio builder.

Logs and exits at initialization. It does not poll, because there is no Queue
abstraction yet, and it does not call news-clusterer, because there is no
clustering endpoint yet. Both arrive in the portfolio-builder spec.
"""

import logging

from ktb_core.logging import setup_logging

from portfolio_builder.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("portfolio-builder started")


if __name__ == "__main__":
    main()
