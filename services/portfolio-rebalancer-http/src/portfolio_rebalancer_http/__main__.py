import logging

import uvicorn
from ktb_core.logging import setup_logging

from portfolio_rebalancer_http.app import app
from portfolio_rebalancer_http.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    logging.getLogger(__name__).info("portfolio-rebalancer-http started")
    # log_config=None keeps uvicorn from replacing the JSON handlers setup_logging installed.
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
