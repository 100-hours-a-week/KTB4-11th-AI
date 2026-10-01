"""Structured JSON logging shared by every service."""

import json
import logging
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
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


@contextmanager
def log_run(log: StructuredLogger, **fields: Any) -> Iterator[dict[str, Any]]:
    """Log `run_start`, then exactly one `run_end` however the block exits.

    Fields put in the yielded dict go on `run_end`. A non-zero `SystemExit` or an
    exception logs it at ERROR; an exception always forces `outcome="error"`.
    """
    started = time.perf_counter()
    end: dict[str, Any] = {}
    log.info("run_start", **fields)

    def elapsed_ms() -> int:
        return round((time.perf_counter() - started) * 1000)

    try:
        yield end
    except SystemExit as exit_:
        code = exit_.code
        failed = code not in (None, 0)
        (log.error if failed else log.info)(
            "run_end",
            **{"outcome": "error" if failed else "ok", **end},
            exit_code=code or 0,
            elapsed_ms=elapsed_ms(),
        )
        raise
    except BaseException as error:
        log.exception(
            "run_end",
            **{
                **end,
                "outcome": "error",
                "error": f"{type(error).__name__}: {error}",
            },
            exit_code=1,
            elapsed_ms=elapsed_ms(),
        )
        raise
    log.info("run_end", **{"outcome": "ok", **end}, exit_code=0, elapsed_ms=elapsed_ms())


def set_logger_level(name: str, level: str) -> None:
    logging.getLogger(name).setLevel(level.upper())


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
