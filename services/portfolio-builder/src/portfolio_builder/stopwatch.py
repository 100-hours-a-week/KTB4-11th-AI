import time
from typing import Self


class Stopwatch:
    def __init__(self) -> None:
        self._started_ns = time.perf_counter_ns()
        self._stopped_ns: int | None = None

    @classmethod
    def start(cls) -> Self:
        return cls()

    def stop(self) -> Self:
        if self._stopped_ns is None:
            self._stopped_ns = time.perf_counter_ns()
        return self

    @property
    def elapsed_ns(self) -> int:
        end = self._stopped_ns if self._stopped_ns is not None else time.perf_counter_ns()
        return end - self._started_ns

    @property
    def elapsed_ms(self) -> int:
        return round(self.elapsed_ns / 1_000_000)

    @property
    def elapsed_seconds(self) -> float:
        return self.elapsed_ns / 1_000_000_000
