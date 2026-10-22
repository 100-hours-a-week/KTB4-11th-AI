import asyncio
import logging
from datetime import UTC, datetime

from ktb_core.logging import get_logger, setup_logging, start_logging
from ktb_telemetry.tracing import tracing

from market_streamer.settings import Settings
from market_streamer.stream import run

log = get_logger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="market-streamer")
    logging.getLogger("kiwoom").setLevel(logging.WARNING)
    with tracing("market-streamer"), start_logging(log, codes_index=settings.index_name):
        asyncio.run(run(settings, datetime.now(UTC)))


if __name__ == "__main__":
    main()
