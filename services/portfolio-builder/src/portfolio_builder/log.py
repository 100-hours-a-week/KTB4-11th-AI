import logging
from collections.abc import Callable
from typing import Any

Log = Callable[..., None]


def make_log(run_id: str) -> Log:
    logger = logging.getLogger("portfolio_builder")

    def log(event: str, level: int = logging.INFO, **fields: Any) -> None:
        logger.log(level, event, extra={"fields": {"run_id": run_id, **fields}})

    return log
