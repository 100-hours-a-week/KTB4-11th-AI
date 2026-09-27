"""Entry point: read settings, build the server, serve /mcp over streamable HTTP."""

import logging

from ktb_core.logging import setup_logging

from market_analyzer_mcp.server import build_server
from market_analyzer_mcp.settings import Settings

log = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    log.info("market-analyzer-mcp listening on %s:%d/mcp", settings.host, settings.port)
    build_server(settings).run(transport="streamable-http", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
