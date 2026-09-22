"""The KOSPI 200 constituent list.

Kiwoom exposes no index-membership endpoint, so this ships as a file reviewed
by hand. Constituents change twice a year.
"""

import csv
from pathlib import Path

__all__ = ["load_from", "load_kospi200"]

_DEFAULT = Path(__file__).with_name("kospi200.csv")


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

    return frozenset(seen)


def load_kospi200() -> frozenset[str]:
    return load_from(_DEFAULT)
