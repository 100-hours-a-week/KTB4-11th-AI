"""Structured JSON logging shared by every service."""

import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

BoundLogger = Callable[..., None]


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service_name": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", *, service_name: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def bind_logger(logger: logging.Logger, **bound: Any) -> BoundLogger:
    """Log an event name with structured fields; `bound` fields go on every line."""

    def log(event: str, level: int = logging.INFO, **fields: Any) -> None:
        logger.log(level, event, extra={"fields": {**bound, **fields}})

    return log
