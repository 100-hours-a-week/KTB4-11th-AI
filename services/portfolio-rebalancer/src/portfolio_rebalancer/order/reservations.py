from collections.abc import Iterable

from portfolio_rebalancer.account.dto import Order
from portfolio_rebalancer.order.dto import PENDING, SELL, Outstanding

PRICE_BANDS: tuple[float, ...] = (0.05, 0.03, 0.01)
TRIGGER_BAND = PRICE_BANDS[0]

TICK_SIZES: tuple[tuple[float, int], ...] = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)
TOP_TICK = 1_000


def tick_size(price: float) -> int:
    for ceiling, tick in TICK_SIZES:
        if price < ceiling:
            return tick
    return TOP_TICK


def on_tick(price: float) -> float:
    tick = tick_size(price)
    return float(round(price / tick) * tick)


def band_for(days_left: int) -> float | None:
    if days_left <= 0:
        return None
    return PRICE_BANDS[max(len(PRICE_BANDS) - days_left, 0)]


def limit_and_trigger(reference: float, days_left: int, side: str) -> tuple[float, float] | None:
    band = band_for(days_left)
    if band is None:
        return None

    if side == SELL:
        return on_tick(reference * (1 + band)), on_tick(reference * (1 - TRIGGER_BAND))
    return on_tick(reference * (1 - band)), on_tick(reference * (1 + TRIGGER_BAND))


def trigger_hit(side: str, trigger: float, price: float) -> bool:
    return price <= trigger if side == SELL else price >= trigger


def outstanding_orders(
    pending_orders: Iterable[Order],
) -> dict[tuple[str, str], Outstanding]:
    sides: dict[tuple[str, str], list[Outstanding]] = {}
    for order in pending_orders:
        if order.order_status != PENDING:
            continue
        key = (order.stock_code, order.order_side)
        price = order.limit_price
        sides.setdefault(key, []).append(
            Outstanding(
                price=price,
                quantity=order.quantity,
            )
        )

    return {key: found[0] for key, found in sides.items() if len(found) == 1}
