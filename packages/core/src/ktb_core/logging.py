"""Structured JSON logging shared by every service."""

import json
import logging
import re
import sys
import time
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

BoundLogger = Callable[..., None]


class StructuredLogger:
    def __init__(self, logger: logging.Logger, **bound: Any) -> None:
        self.logger = logger
        self.bound = bound

    def bind(self, **fields: Any) -> "StructuredLogger":
        return StructuredLogger(self.logger, **self.bound, **fields)

    def _log(self, level: int, event: str, *, exc_info: bool = False, **fields: Any) -> None:
        self.logger.log(level, event, extra={"fields": {**self.bound, **fields}}, exc_info=exc_info)

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, **fields)

    def exception(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, exc_info=True, **fields)


def get_logger(name: str, **bound: Any) -> StructuredLogger:
    return StructuredLogger(logging.getLogger(name), **bound)


class start_logging:
    def __init__(self, log: StructuredLogger, **fields: Any) -> None:
        self.log = log
        self.fields = fields
        self.end: dict[str, Any] = {}

    def __enter__(self) -> dict[str, Any]:
        self.started = time.perf_counter()
        self.log.info("run_start", **self.fields)
        return self.end

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        elapsed_ms = round((time.perf_counter() - self.started) * 1000)
        if error is None:
            self.log.info(
                "run_end", **{"outcome": "ok", **self.end}, exit_code=0, elapsed_ms=elapsed_ms
            )
        elif isinstance(error, SystemExit):
            failed = error.code not in (None, 0)
            (self.log.error if failed else self.log.info)(
                "run_end",
                **{"outcome": "error" if failed else "ok", **self.end},
                exit_code=error.code or 0,
                elapsed_ms=elapsed_ms,
            )
        else:
            self.log.exception(
                "run_end",
                **{**self.end, "outcome": "error", "error": f"{type(error).__name__}: {error}"},
                exit_code=1,
                elapsed_ms=elapsed_ms,
            )


def set_logger_level(name: str, level: str) -> None:
    logging.getLogger(name).setLevel(level.upper())


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str, sensitive_query_params: Collection[str] = ()) -> None:
        super().__init__()
        self.service_name = service_name
        names = "|".join(map(re.escape, sensitive_query_params))
        # The JSON line writes a quote as \" and a newline as \n, so a backslash ends the value.
        self.sensitive = re.compile(rf"({names})=[^&\s\"'\\]+") if names else None

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
        line = json.dumps(payload, ensure_ascii=False, default=str)
        return self.sensitive.sub(r"\1=***", line) if self.sensitive else line


def setup_logging(
    level: str = "INFO", *, service_name: str, sensitive_query_params: Collection[str] = ()
) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name, sensitive_query_params))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def bind_logger(logger: logging.Logger, **bound: Any) -> BoundLogger:
    """Log an event name with structured fields; `bound` fields go on every line."""

    def log(event: str, level: int = logging.INFO, **fields: Any) -> None:
        logger.log(level, event, extra={"fields": {**bound, **fields}})

    return log
