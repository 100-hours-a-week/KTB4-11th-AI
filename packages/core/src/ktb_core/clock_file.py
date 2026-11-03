import os
from datetime import datetime
from pathlib import Path


def write_clock(path: str | Path, as_of: datetime) -> None:
    if as_of.utcoffset() is None:
        raise ValueError("clock time must be timezone-aware")
    target = Path(path)
    staged = target.with_name(f".{target.name}.tmp")
    staged.write_text(as_of.isoformat())
    os.replace(staged, target)


def read_clock(path: str | Path) -> datetime:
    as_of = datetime.fromisoformat(Path(path).read_text().strip())
    if as_of.utcoffset() is None:
        raise ValueError("clock time must be timezone-aware")
    return as_of
