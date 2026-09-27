"""Normalise a symbol and render readings as text. Pure: no I/O, no MCP."""

import re
from datetime import datetime

from ktb_market_analyzer import Candles, interpret
from ktb_market_analyzer.descriptions import DESCRIPTIONS

__all__ = ["FIELDS", "describe", "normalize_symbol"]

FIELDS = tuple(DESCRIPTIONS)

# Six alphanumeric characters, so "0126Z0" passes as well as "005930". An "A"
# prefix and a .KS/.KQ suffix are both stripped, because a model asking about a
# stock may carry the code in any of those forms.
_SYMBOL = re.compile(r"A?([0-9A-Z]{6})(?:\.K[SQ])?")


def normalize_symbol(raw: str) -> str | None:
    """The bare six-character code, or ``None`` when ``raw`` is not a code at all."""
    match = _SYMBOL.fullmatch(raw.strip().upper())
    return match.group(1) if match else None


def describe(
    symbol: str, timeframe: str, candles: Candles, newest: datetime, *, sessions_mixed: bool = False
) -> str:
    """Every indicator over ``candles``, one line each, with the verdict and its reason."""
    scope = "all sessions" if sessions_mixed else "regular session"
    lines = [
        f"{symbol} {timeframe}: {candles.close.size} candles ({scope}), "
        f"newest {newest.isoformat()} (may still be in progress), "
        f"close {candles.close[-1]:g}"
    ]
    for field in FIELDS:
        reading = interpret(field, candles)
        if reading.value is None:
            verdict = "insufficient data"
        elif reading.comment is None:
            verdict = f"{reading.value:.2f}"
        else:
            verdict = f"{reading.value:.2f} [{reading.comment}] {reading.comment_reasoning}"
        lines.append(f"- {field}: {verdict}\n  {reading.description}")
    return "\n".join(lines)
