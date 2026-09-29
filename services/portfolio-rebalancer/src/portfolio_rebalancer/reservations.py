"""An order's two reservation prices, and reading a pair back. Pure: no I/O."""

from collections.abc import Iterable, Mapping

__all__ = [
    "PRICE_BANDS",
    "find_pairs",
    "next_rung",
    "read_reservation",
    "reservation_prices",
]

PENDING = "pending"

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


def find_pairs(
    pending_orders: Iterable[Mapping[str, object]],
) -> dict[tuple[str, str], tuple[float, float]]:
    """The outstanding reservation pairs, keyed by stock code and side.

    Two pending orders on the same stock and the same side are one pair. A lone order
    means the other side already filled, and three means a state this service did not
    create; neither is narrowed, because a rung placed against a moved position is worse
    than leaving it to the current one.
    """
    sides: dict[tuple[str, str], list[float]] = {}
    for order in pending_orders:
        if order.get("status") != PENDING:
            continue
        key = (str(order["stock_code"]), str(order["order_type"]))
        sides.setdefault(key, []).append(float(order["price"]))  # type: ignore[arg-type]

    return {key: (min(prices), max(prices)) for key, prices in sides.items() if len(prices) == 2}


def next_rung(low: float, high: float) -> tuple[float, float] | None:
    """The narrower pair to replace an outstanding one with, or None to go to market.

    The day is recovered from the pair rather than stored, so the rung after the last
    band is the market order that guarantees the fill.
    """
    reference, day = read_reservation(low, high)
    return reservation_prices(reference, day + 1)
