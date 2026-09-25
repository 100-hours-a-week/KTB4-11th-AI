"""Data-transfer type for one index's constituent.

Kept deliberately thin: an index's *name* (e.g. "KOSPI200" for index_code
"201") is not part of what Kiwoom's ka20002 response carries per row, so it
is not carried here either -- see repository.py's INDEX_NAMES for where that
mapping lives instead.
"""

from dataclasses import dataclass

__all__ = ["IndexMember"]


@dataclass(frozen=True)
class IndexMember:
    index_code: str
    symbol: str
    stock_name: str
