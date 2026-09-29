"""An order's two reservation prices, and reading a pair back. Pure: no I/O."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

__all__ = [
    "PRICE_BANDS",
    "TICK_SIZES",
    "Pair",
    "on_tick",
    "find_pairs",
    "next_rung",
    "read_reservation",
    "reservation_prices",
]

PENDING = "pending"

# KRX moves in these steps, and the Backend answers 400 for a price that is not on one.
# (upper bound exclusive, step)
TICK_SIZES: tuple[tuple[float, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)
TOP_TICK = 1_000


@dataclass(frozen=True)
class Pair:
    """An outstanding reservation: its two prices and the quantity still to fill."""

    low: float
    high: float
    quantity: int


# Day three ends in a market order rather than a fourth band, so every order fills
# within three trading days. Narrowing alone would not guarantee that: a 1% band is
# harder to reach than a 5% one.
PRICE_BANDS: tuple[float, ...] = (0.05, 0.03, 0.01)


def tick_size(price: float) -> int:
    """The step KRX quotes this price in."""
    for ceiling, tick in TICK_SIZES:
        if price < ceiling:
            return tick
    return TOP_TICK


def on_tick(price: float) -> float:
    """The nearest price KRX will accept.

    Rounding to nearest rather than up or down keeps the pair symmetric about its
    reference, which is what lets the day be read back out of it.
    """
    tick = tick_size(price)
    return float(round(price / tick) * tick)


def reservation_prices(reference: float, day: int) -> tuple[float, float] | None:
    """The low and high price for a day, or None once the ladder is at market.

    Both are placed on a KRX tick here rather than left to the Backend, which rejects an
    off-tick price with a 400 instead of rounding it.
    """
    if day >= len(PRICE_BANDS):
        return None
    band = PRICE_BANDS[day]
    return on_tick(reference * (1 - band)), on_tick(reference * (1 + band))


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
) -> dict[tuple[str, str], Pair]:
    """The outstanding reservation pairs, keyed by stock code and side.

    Two pending orders on the same stock and the same side are one pair. A lone order
    means the other side already filled, and three means a state this service did not
    create; neither is narrowed, because a rung placed against a moved position is worse
    than leaving it to the current one.
    """
    sides: dict[tuple[str, str], list[tuple[float, int]]] = {}
    for order in pending_orders:
        if order.get("status") != PENDING:
            continue
        key = (str(order["stock_code"]), str(order["order_type"]))
        sides.setdefault(key, []).append(
            (float(order["price"]), int(order["amount"]))  # type: ignore[arg-type]
        )

    # Both sides go out with the full quantity, so the smaller outstanding amount is what
    # a partial fill left behind: that is what still has to be filled.
    return {
        key: Pair(
            low=min(price for price, _ in side),
            high=max(price for price, _ in side),
            quantity=min(quantity for _, quantity in side),
        )
        for key, side in sides.items()
        if len(side) == 2
    }


def next_rung(low: float, high: float) -> tuple[float, float] | None:
    """The narrower pair to replace an outstanding one with, or None to go to market.

    The day is recovered from the pair rather than stored, so the rung after the last
    band is the market order that guarantees the fill.
    """
    reference, day = read_reservation(low, high)
    return reservation_prices(reference, day + 1)
