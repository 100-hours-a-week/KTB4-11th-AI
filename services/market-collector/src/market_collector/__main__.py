"""Entry point for the market-collector."""

import logging
import sys

from ktb_core.logging import setup_logging

from market_collector.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    log = logging.getLogger(__name__)

    command = sys.argv[1] if len(sys.argv) > 1 else None
    if command is None:
        log.info("market-collector started")
        return

    log.error("unknown subcommand: %s", command)
    raise SystemExit(2)
