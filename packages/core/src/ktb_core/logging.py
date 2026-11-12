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

    def debug(self, event: str, **fields: Any) -> None:
        self._log(logging.DEBUG, event, **fields)

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
    def __init__(
        self,
        service_name: str,
        sensitive_query_params: Collection[str] = (),
        *,
        sensitive_values: Collection[str] = (),
        redact_urls: bool = False,
    ) -> None:
        super().__init__()
        self.service_name = service_name
        names = "|".join(map(re.escape, sensitive_query_params))
        self.sensitive = re.compile(rf"({names})=[^&\s\"'\\]+") if names else None
        self.urls = re.compile(r"https?://\S+", re.IGNORECASE) if redact_urls else None
        self.secrets = sorted(
            {
                variant
                for value in sensitive_values
                if value
                for variant in (
                    value,
                    repr(value)[1:-1],
                    repr(value.encode(errors="backslashreplace"))[2:-1],
                )
            },
            key=len,
            reverse=True,
        )

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

        def redact(value: Any) -> Any:
            if isinstance(value, str):
                spans: list[tuple[int, int, bool]] = []
                if self.urls:
                    spans.extend((m.start(), m.end(), True) for m in self.urls.finditer(value))
                if self.sensitive:
                    spans.extend(
                        (m.end(1) + 1, m.end(), False) for m in self.sensitive.finditer(value)
                    )
                for secret in self.secrets:
                    start = value.find(secret)
                    while start != -1:
                        spans.append((start, start + len(secret), False))
                        start = value.find(secret, start + 1)
                if not spans:
                    return value

                merged: list[tuple[int, int, bool]] = []
                for start, end, is_url in sorted(spans):
                    if merged and start < merged[-1][1]:
                        previous_start, previous_end, previous_url = merged[-1]
                        merged[-1] = (
                            previous_start,
                            max(previous_end, end),
                            previous_url or is_url,
                        )
                    else:
                        merged.append((start, end, is_url))

                parts: list[str] = []
                position = 0
                for start, end, is_url in merged:
                    parts.append(value[position:start])
                    parts.append("<redacted-url>" if is_url else "***")
                    position = end
                parts.append(value[position:])
                return "".join(parts)
            elif isinstance(value, dict):
                grouped: dict[Any, list[Any]] = {}
                for key, item in value.items():
                    grouped.setdefault(redact(key), []).append(redact(item))
                return {
                    key: items[0] if len(items) == 1 else items for key, items in grouped.items()
                }
            elif isinstance(value, list | tuple):
                return [redact(item) for item in value]
            return value

        line = json.dumps(
            redact(payload) if self.secrets or self.urls or self.sensitive else payload,
            ensure_ascii=False,
            default=lambda value: redact(str(value)),
        )
        return line


def setup_logging(
    level: str = "INFO",
    *,
    service_name: str,
    sensitive_query_params: Collection[str] = (),
    sensitive_values: Collection[str] = (),
    redact_urls: bool = False,
) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            service_name,
            sensitive_query_params,
            sensitive_values=sensitive_values,
            redact_urls=redact_urls,
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def bind_logger(logger: logging.Logger, **bound: Any) -> BoundLogger:
    """Log an event name with structured fields; `bound` fields go on every line."""

    def log(event: str, level: int = logging.INFO, **fields: Any) -> None:
        logger.log(level, event, extra={"fields": {**bound, **fields}})

    return log
