from collections.abc import Iterable, Mapping

from portfolio_rebalancer.order.dto import PENDING, SELL, Outstanding

# A limit order fills as soon as the market reaches it, so only the far side can be left
# waiting: a sell above the market, a buy below it. The near side becomes a trigger this
# service watches, and crossing it sends the order at market.
#
#     sell   limit at reference + band     market if the price falls to reference - 5%
#     buy    limit at reference - band     market if the price rises to reference + 5%
#
# Day three ends in a market order rather than a fourth band, so every order fills within
# the week. Narrowing alone would not guarantee that: a 1% band is harder to reach than a
# 5% one.
PRICE_BANDS: tuple[float, ...] = (0.05, 0.03, 0.01)

# The trigger does not narrow with the limit. It is the point where waiting stops being
# worth it, and that does not get closer just because fewer days remain -- tightening it
# would send orders at market on a move the limit was still willing to wait out.
TRIGGER_BAND = PRICE_BANDS[0]

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
    # None once the sessions run out: the order then goes at market outright. Both prices
    # sit on a KRX tick, the limit because the Backend rejects it otherwise and the trigger
    # so it is comparable with a quoted price.
    band = band_for(days_left)
    if band is None:
        return None

    if side == SELL:
        return on_tick(reference * (1 + band)), on_tick(reference * (1 - TRIGGER_BAND))
    return on_tick(reference * (1 - band)), on_tick(reference * (1 + TRIGGER_BAND))


def trigger_hit(side: str, trigger: float, price: float) -> bool:
    # A sell waits for a rise, so a fall to the trigger means the chance is gone; a buy waits
    # for a dip, so a rise means the dip is not coming.
    return price <= trigger if side == SELL else price >= trigger


def outstanding_orders(
    pending_orders: Iterable[Mapping[str, object]],
) -> dict[tuple[str, str], Outstanding]:
    # Keyed by stock code and side. More than one order for a key is a state this service
    # did not create, so it is left alone rather than guessing which is ours.
    sides: dict[tuple[str, str], list[Outstanding]] = {}
    for order in pending_orders:
        if order.get("status") != PENDING:
            continue
        key = (str(order["stock_code"]), str(order["order_type"]))
        sides.setdefault(key, []).append(
            Outstanding(price=float(order["price"]), quantity=int(order["amount"]))  # type: ignore[arg-type]
        )

    return {key: found[0] for key, found in sides.items() if len(found) == 1}
