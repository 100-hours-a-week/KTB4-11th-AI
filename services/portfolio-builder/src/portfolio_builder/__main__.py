"""Entry point for the portfolio-builder."""

import logging

from ktb_core.logging import setup_logging

from portfolio_builder.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("portfolio-builder started")


if __name__ == "__main__":
    main()
