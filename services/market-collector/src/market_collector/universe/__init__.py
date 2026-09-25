"""The KOSPI 200 constituent list.

Kiwoom exposes no index-membership endpoint, so this ships as a file reviewed
by hand. Constituents change twice a year.
"""

import csv
from pathlib import Path

__all__ = ["EmptyUniverseError", "load_from", "load_kospi200"]

_DEFAULT = Path(__file__).with_name("kospi200.csv")


class EmptyUniverseError(RuntimeError):
    """Raised when the constituent file has no codes.

    An empty universe is a misconfiguration — as of this writing
    ``universe/kospi200.csv`` holds only its header because the KRX export
    has not landed yet — not a transient condition to tolerate. Left
    unchecked it flows silently into ``shard([], n)`` returning ``[]`` and
    then ``ThreadPoolExecutor(max_workers=0)`` raising an opaque
    ``ValueError`` far from the real cause. This fails loudly at the source
    instead, the same treatment the design gives Kiwoom's
    ``return_code=3`` (unregistered IP): a startup misconfiguration, not
    something to retry around.
    """


def load_from(path: Path) -> frozenset[str]:
    codes: list[str] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            code = (row.get("code") or "").strip()
            if len(code) != 6 or not code.isdigit():
                raise ValueError(f"not a six-digit stock code: {code!r}")
            codes.append(code)

    seen = set()
    for code in codes:
        if code in seen:
            raise ValueError(f"duplicate stock code: {code}")
        seen.add(code)

    if not seen:
        raise EmptyUniverseError(
            f"no KOSPI 200 constituents found in {path}; "
            "the KRX export has not been added to this file yet"
        )

    return frozenset(seen)


def load_kospi200() -> frozenset[str]:
    return load_from(_DEFAULT)
