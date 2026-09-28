"""An order's two reservation prices, and reading a pair back. Pure: no I/O."""

__all__ = ["PRICE_BANDS", "read_reservation", "reservation_prices"]

# Day three ends in a market order rather than a fourth band, so every order fills
# within three trading days. Narrowing alone would not guarantee that: a 1% band is
# harder to reach than a 5% one.
PRICE_BANDS: tuple[float, ...] = (0.05, 0.03, 0.01)


def reservation_prices(reference: float, day: int) -> tuple[float, float] | None:
    """The low and high price for a day, or None once the ladder is at market."""
    if day >= len(PRICE_BANDS):
        return None
    band = PRICE_BANDS[day]
    return reference * (1 - band), reference * (1 + band)


def read_reservation(low: float, high: float) -> tuple[float, int]:
    """The reference price and the day, recovered from an outstanding pair.

    Neither is stored. Two prices fix both, and the bands are two percentage points
    apart, so the Backend's tick rounding cannot push one into another.
    """
    reference = (low + high) / 2
    ratio = (high - low) / (high + low)
    day = min(range(len(PRICE_BANDS)), key=lambda i: abs(PRICE_BANDS[i] - ratio))
    return reference, day
