"""Structured JSON logging shared by every service."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

# Matches both plain and backslash-escaped quotes consistently using backreferences.
# The \1 ensures that if the opening quote is escaped, all quotes in the pattern are escaped.
_TOKEN_FIELD = re.compile(r'(\\?)"(access|refresh|id)_token\1"\s*:\s*\1"[^"\\]*\1"')
_JWT = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
_OPENROUTER_KEY = re.compile(r"sk-or-[\w-]+")


def redact(line: str) -> str:
    line = _TOKEN_FIELD.sub(r'\1"\2_token\1":\1"[redacted]\1"', line)
    line = _JWT.sub("[redacted-jwt]", line)
    return _OPENROUTER_KEY.sub("[redacted-key]", line)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, ensure_ascii=False, default=str))


def setup_logging(level: str = "INFO", *, service: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
