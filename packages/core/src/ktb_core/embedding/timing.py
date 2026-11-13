import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from ktb_core.logging import get_logger

log = get_logger(__name__)
search_id: ContextVar[str | None] = ContextVar("disclosure_search_id", default=None)


@contextmanager
def timing(stage: str, **fields):
    token = search_id.set(search_id.get() or uuid4().hex)
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield fields
    except BaseException as error:
        outcome = "error"
        fields["error_type"] = type(error).__name__
        raise
    finally:
        (log.info if outcome == "ok" else log.error)(
            "disclosure_timing",
            stage=stage,
            search_id=search_id.get(),
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            outcome=outcome,
            **fields,
        )
        search_id.reset(token)


def server_timings(result: dict, request_id: str) -> dict:
    fields = {
        f"modal_{key}": None
        for key in (
            "inference_ms",
            "request_ms",
            "model_load_ms",
            "application_startup_ms",
            "restore_hook_ms",
            "cold_start_ms",
            "first_request",
            "container_id",
            "initialization_id",
            "function_call_id",
            "startup_kind",
        )
    }
    source = result.get("timing")
    if not isinstance(source, dict) or source.get("request_id") != request_id:
        return fields
    for key in fields:
        name = key.removeprefix("modal_")
        value = source.get(name)
        if name.endswith("_ms"):
            if type(value) in (int, float):
                try:
                    valid = math.isfinite(value) and value >= 0
                except OverflowError:
                    valid = False
                if valid:
                    fields[key] = value
        elif name == "first_request":
            if type(value) is bool:
                fields[key] = value
        elif name == "startup_kind":
            if value in ("fresh", "snapshot_restore", "warm", "unknown"):
                fields[key] = value
        elif isinstance(value, str) and len(value) <= 128:
            fields[key] = value
    return fields
