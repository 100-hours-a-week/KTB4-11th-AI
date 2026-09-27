import logging

import uvicorn
from ktb_core.logging import setup_logging

from market_analyzer_mcp.server import build_app
from market_analyzer_mcp.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("market-analyzer-mcp started")
    # log_config=None keeps uvicorn from replacing the JSON handlers setup_logging installed.
    uvicorn.run(build_app(settings.host), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
