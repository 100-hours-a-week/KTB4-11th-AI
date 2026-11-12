from datetime import datetime
from pathlib import Path

from ktb_core.clock_file import write_clock


class FileClock:
    def __init__(self, path: str | Path):
        self._path = path

    def set(self, as_of: datetime) -> None:
        write_clock(self._path, as_of)


class NullClock:
    def set(self, as_of: datetime) -> None:
        pass
