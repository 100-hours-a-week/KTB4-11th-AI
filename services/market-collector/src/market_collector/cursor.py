"""Per-symbol, per-timeframe backfill progress.

ka10080 ignores any date parameter, so a symbol's history can only be walked
backwards one page at a time with next-key. A run that dies partway through
cannot restart from a date, which makes persisting the key the difference
between resuming and starting over.

State is a single JSON file because it is small, human-readable when a run
goes wrong, and needs no service to be up.
"""

import json
import os
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

__all__ = ["Cursor", "CursorStore"]


@dataclass(frozen=True)
class Cursor:
    next_key: str | None = None
    oldest: str | None = None
    pages: int = 0
    done: bool = False


class CursorStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._state: dict[str, dict[str, object]] = self._load()
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self._path.is_file():
            return {}
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _key(symbol: str, timeframe: str) -> str:
        return f"{symbol}|{timeframe}"

    def _get_unlocked(self, symbol: str, timeframe: str) -> Cursor:
        raw = self._state.get(self._key(symbol, timeframe))
        if not isinstance(raw, dict):
            return Cursor()
        return Cursor(
            next_key=cast(str | None, raw.get("next_key")),
            oldest=cast(str | None, raw.get("oldest")),
            pages=int(cast(int, raw.get("pages", 0))),
            done=bool(raw.get("done", False)),
        )

    def get(self, symbol: str, timeframe: str) -> Cursor:
        with self._lock:
            return self._get_unlocked(symbol, timeframe)

    def _put(self, symbol: str, timeframe: str, cursor: Cursor) -> Cursor:
        self._state[self._key(symbol, timeframe)] = asdict(cursor)
        self._write()
        return cursor

    def advance(
        self, symbol: str, timeframe: str, next_key: str | None, oldest: str | None
    ) -> Cursor:
        with self._lock:
            current = self._get_unlocked(symbol, timeframe)
            new_cursor = Cursor(
                next_key=next_key,
                oldest=oldest or current.oldest,
                pages=current.pages + 1,
                done=False,
            )
            return self._put(symbol, timeframe, new_cursor)

    def finish(self, symbol: str, timeframe: str) -> Cursor:
        with self._lock:
            current = self._get_unlocked(symbol, timeframe)
            new_cursor = Cursor(
                next_key=None, oldest=current.oldest, pages=current.pages, done=True
            )
            return self._put(symbol, timeframe, new_cursor)

    def pending(self, symbols: Iterable[str], timeframes: Iterable[str]) -> list[tuple[str, str]]:
        frames = list(timeframes)
        with self._lock:
            return [
                (symbol, timeframe)
                for symbol in symbols
                for timeframe in frames
                if not self._get_unlocked(symbol, timeframe).done
            ]

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self._path)
