import pytest
from portfolio_rebalancer.account.dto import Order
from portfolio_rebalancer.order.dto import Outstanding
from portfolio_rebalancer.order.reservations import (
    PRICE_BANDS,
    TICK_SIZES,
    band_for,
    limit_and_trigger,
    on_tick,
    outstanding_orders,
    tick_size,
    trigger_hit,
)

REFERENCES = (1_234.0, 9_050.0, 78_000.0, 155_500.0, 412_000.0, 1_800_000.0)
BOUNDARIES = tuple(
    price
    for ceiling, _ in TICK_SIZES
    for price in (float(ceiling - 1), float(ceiling), float(ceiling + 1))
)
LADDER_DAYS = len(PRICE_BANDS)


def pending(stock_code="005930", order_side="buy", limit_price=74_100, quantity=100, **extra):
    """A pending order as `GET /api/v1/users/ai-server` spells it.

    `order_side` is buy or sell; `order_type` is limit or market. They are different
    fields, and reading one for the other files every order under the wrong key.
    """
    return Order.model_validate(
        {
            "order_id": 1,
            "stock_code": stock_code,
            "order_side": order_side,
            "order_status": "pending",
            "order_type": "limit",
            "limit_price": limit_price,
            "quantity": quantity,
            "current_stock_price": 78_000,
        }
        | extra
    )


# ---- the KRX tick ----


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (1_999.0, 1),
        (2_000.0, 5),
        (4_999.0, 5),
        (5_000.0, 10),
        (19_999.0, 10),
        (20_000.0, 50),
        (49_999.0, 50),
        (50_000.0, 100),
        (199_999.0, 100),
        (200_000.0, 500),
        (499_999.0, 500),
        (500_000.0, 1_000),
        (3_000_000.0, 1_000),
    ],
)
def test_the_tick_table_matches_the_exchange(price, expected):
    """The Backend answers 400 for a price off the tick, so this table is load-bearing."""
    assert tick_size(price) == expected


@pytest.mark.parametrize("price", [*REFERENCES, *BOUNDARIES])
def test_every_rounded_price_lands_on_a_tick(price):
    rounded = on_tick(price)

    assert rounded % tick_size(rounded) == 0


def test_rounding_goes_to_the_nearest_tick_not_up_or_down():
    assert on_tick(75_660.0) == 75_700.0
    assert on_tick(75_640.0) == 75_600.0


# ---- the band ----


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [(3, PRICE_BANDS[0]), (2, PRICE_BANDS[1]), (1, PRICE_BANDS[2])],
)
def test_the_band_comes_from_the_days_left(days_left, expected):
    """Selling and buying share one deadline, so an order that starts late starts on a
    narrower band rather than restarting the ladder."""
    assert band_for(days_left) == expected


@pytest.mark.parametrize("days_left", [0, -1, -5])
def test_no_days_left_means_market(days_left):
    assert band_for(days_left) is None


def test_more_days_than_the_ladder_has_is_the_widest_band():
    assert band_for(LADDER_DAYS + 5) == PRICE_BANDS[0]


def test_the_bands_narrow():
    widths = [band_for(days) for days in range(LADDER_DAYS, 0, -1)]

    assert widths == sorted(widths, reverse=True)


# ---- the limit and the trigger ----


def test_a_sell_waits_above_the_market_and_triggers_below_it():
    """A sell limit below the market fills at once, which is the opposite of waiting. So
    the order goes above, and a fall to the other side sends it at market."""
    limit, trigger = limit_and_trigger(78_000.0, 3, "sell")

    assert limit == 81_900.0
    assert trigger == 74_100.0
    assert limit > 78_000.0 > trigger


def test_a_buy_waits_below_the_market_and_triggers_above_it():
    """A buy limit above the market fills at once for the same reason."""
    limit, trigger = limit_and_trigger(78_000.0, 3, "buy")

    assert limit == 74_100.0
    assert trigger == 81_900.0
    assert trigger > 78_000.0 > limit


def test_the_two_sides_are_mirror_images_of_each_other():
    """Mirrored about the reference: the sell's limit is where the buy's trigger is."""
    sell_limit, sell_trigger = limit_and_trigger(78_000.0, 3, "sell")
    buy_limit, buy_trigger = limit_and_trigger(78_000.0, 3, "buy")

    assert (sell_limit, sell_trigger) == (buy_trigger, buy_limit)


def test_the_trigger_does_not_narrow_with_the_limit():
    """Waiting stops being worth it at the same price whatever day it is. Tightening the
    trigger would send an order at market on a move the limit was still willing to wait
    out."""
    triggers = {limit_and_trigger(78_000.0, days, "sell")[1] for days in (3, 2, 1)}
    limits = {limit_and_trigger(78_000.0, days, "sell")[0] for days in (3, 2, 1)}

    assert len(triggers) == 1
    assert len(limits) == 3


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_the_trigger_stays_at_the_widest_band(side):
    from portfolio_rebalancer.order.reservations import TRIGGER_BAND

    _, trigger = limit_and_trigger(78_000.0, 1, side)
    away = abs(trigger - 78_000.0) / 78_000.0

    assert away == pytest.approx(TRIGGER_BAND, abs=0.001)
    assert TRIGGER_BAND == PRICE_BANDS[0]


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_no_days_left_has_no_limit_and_no_trigger(side):
    assert limit_and_trigger(78_000.0, 0, side) is None


@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("reference", [*REFERENCES, *BOUNDARIES])
def test_both_prices_land_on_a_tick(side, reference):
    for days in range(1, LADDER_DAYS + 1):
        limit, trigger = limit_and_trigger(reference, days, side)

        assert limit % tick_size(limit) == 0
        assert trigger % tick_size(trigger) == 0


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_the_limit_narrows_towards_the_reference(side):
    distances = [
        abs(limit_and_trigger(78_000.0, days, side)[0] - 78_000.0)
        for days in range(LADDER_DAYS, 0, -1)
    ]

    assert distances == sorted(distances, reverse=True)


# ---- the trigger firing ----


def test_a_sell_triggers_when_the_price_falls_to_it():
    """The chance of selling high is gone, so getting out at market beats holding on."""
    assert trigger_hit("sell", 74_100.0, 74_100.0) is True
    assert trigger_hit("sell", 74_100.0, 70_000.0) is True
    assert trigger_hit("sell", 74_100.0, 74_200.0) is False


def test_a_buy_triggers_when_the_price_rises_to_it():
    """The dip is not coming, so buying at market beats waiting for one."""
    assert trigger_hit("buy", 81_900.0, 81_900.0) is True
    assert trigger_hit("buy", 81_900.0, 90_000.0) is True
    assert trigger_hit("buy", 81_900.0, 81_800.0) is False


def test_the_sides_trigger_in_opposite_directions():
    """Confusing them would send an order at market the moment it was placed."""
    price = 78_000.0

    assert trigger_hit("sell", 80_000.0, price) is True
    assert trigger_hit("buy", 80_000.0, price) is False


# ---- what is still working ----


def test_one_pending_order_is_what_is_working():
    limit, _ = limit_and_trigger(78_000.0, 3, "buy")

    working = outstanding_orders([pending(price=limit, amount=100)])

    assert working == {("005930", "buy"): Outstanding(price=limit, quantity=100)}


def test_two_orders_on_one_side_are_left_alone():
    """A state this service did not create: it places one order per stock and side."""
    orders = [pending(price=74_100.0), pending(price=81_900.0)]

    assert outstanding_orders(orders) == {}


def test_the_two_sides_of_a_stock_are_separate():
    orders = [pending(order_side="buy"), pending(order_side="sell")]

    assert set(outstanding_orders(orders)) == {("005930", "buy"), ("005930", "sell")}


def test_two_stocks_each_have_their_own():
    orders = [pending(stock_code="005930"), pending(stock_code="000660", limit_price=412_000)]

    assert set(outstanding_orders(orders)) == {("005930", "buy"), ("000660", "buy")}


def test_an_order_that_is_not_pending_is_ignored():
    settled = pending(order_status="filled")

    assert outstanding_orders([settled]) == {}


def test_the_outstanding_quantity_is_what_is_left():
    """A partial fill leaves less, and that is what a market order has to ask for."""
    working = outstanding_orders([pending(quantity=60)])

    assert working[("005930", "buy")].quantity == 60


def test_nothing_pending_is_nothing_working():
    assert outstanding_orders([]) == {}
