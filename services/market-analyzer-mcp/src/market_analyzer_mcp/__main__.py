"""Entry point: read settings and serve /mcp over streamable HTTP."""

import logging

import uvicorn
from ktb_core.logging import setup_logging

from market_analyzer_mcp.server import build_app
from market_analyzer_mcp.settings import Settings

log = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    log.info("market-analyzer-mcp listening on %s:%d/mcp", settings.host, settings.port)
    # log_config=None keeps uvicorn from replacing the handlers setup_logging installed.
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
