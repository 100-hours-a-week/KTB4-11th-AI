import json
import os
import time
from contextlib import contextmanager
from uuid import uuid4


def emit(event: str, **fields) -> None:
    print(json.dumps({"message": event, **fields}, allow_nan=False), flush=True)


class InferenceTiming:
    def __init__(self, model: str) -> None:
        self.model = model
        self.initialization_id = uuid4().hex
        self.load_container_id = os.environ.get("MODAL_TASK_ID")
        self.load_started = time.perf_counter()
        self.model_load_ms = None
        self.first_request = True

    def loaded(self) -> None:
        self.model_load_ms = round((time.perf_counter() - self.load_started) * 1000, 3)
        emit(
            "modal_model_loaded",
            model=self.model,
            initialization_id=self.initialization_id,
            container_id=self.load_container_id,
            model_load_ms=self.model_load_ms,
        )

    def ready(self) -> None:
        started = time.perf_counter()
        self.container_id = os.environ.get("MODAL_TASK_ID")
        self.first_request = True
        self.startup_kind = "unknown"
        if self.load_container_id and self.container_id and self.model_load_ms is not None:
            self.startup_kind = (
                "fresh" if self.load_container_id == self.container_id else "snapshot_restore"
            )
        self.application_startup_ms = (
            round((time.perf_counter() - self.load_started) * 1000, 3)
            if self.startup_kind == "fresh"
            else None
        )
        self.restore_hook_ms = round((time.perf_counter() - started) * 1000, 3)
        emit(
            "modal_container_ready",
            model=self.model,
            container_id=self.container_id,
            initialization_id=self.initialization_id,
            startup_kind=self.startup_kind,
            application_startup_ms=self.application_startup_ms,
            restore_hook_ms=self.restore_hook_ms,
            cold_start_ms=None,
        )

    @contextmanager
    def request(self, payload: dict, stage: str):
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            request_id = uuid4().hex
        first = self.first_request
        self.first_request = False
        fields = {
            "request_id": request_id,
            "container_id": self.container_id,
            "initialization_id": self.initialization_id,
            "first_request": first,
            "startup_kind": self.startup_kind if first else "warm",
            "model_load_ms": self.model_load_ms if first and self.startup_kind == "fresh" else 0,
            "application_startup_ms": self.application_startup_ms if first else 0,
            "restore_hook_ms": self.restore_hook_ms if first else 0,
            "cold_start_ms": None,
        }
        started = time.perf_counter()
        outcome = "ok"
        try:
            yield fields
        except BaseException as error:
            outcome = "error"
            fields["error_type"] = type(error).__name__
            raise
        finally:
            fields["inference_ms"] = round((time.perf_counter() - started) * 1000, 3)
            emit("modal_inference", model=self.model, stage=stage, outcome=outcome, **fields)


def request_timing(owner, payload: dict, stage: str):
    state = getattr(owner, "observation", None)
    if state is None:
        state = owner.observation = InferenceTiming("unknown")
        state.ready()
    return state.request(payload, stage)
