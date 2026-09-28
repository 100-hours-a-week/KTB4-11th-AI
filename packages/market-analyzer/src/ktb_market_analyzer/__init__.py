"""Deterministic TA-Lib feature extraction for 척척개미단.

Two calls, and nothing else. :func:`interpret` answers about a moment and needs
price data; :func:`get_basic_market_data` answers about an indicator and needs
none. Between them they carry the number, the verdict, the arithmetic behind the
verdict, and what the indicator measures — the four things a caller used to
assemble from four separate imports.

The pieces those two are built from — the indicator functions, the verdict rules,
the text registries — live in :mod:`indicators`, :mod:`comments` and
:mod:`descriptions`. They stay importable for a consumer that needs whole series
rather than one moment, such as a collector writing every candle to storage, but
they are not part of this surface.
"""

from ktb_market_analyzer.readings import (
    Candles,
    Reading,
    get_basic_market_data,
    interpret,
)

__all__ = ["Candles", "Reading", "get_basic_market_data", "interpret"]
