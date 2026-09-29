import pytest
from portfolio_rebalancer.reservations import (
    PRICE_BANDS,
    Pair,
    find_pairs,
    next_rung,
    read_reservation,
    reservation_prices,
)

REFERENCES = (1_234.0, 9_050.0, 78_000.0, 155_500.0, 412_000.0, 1_800_000.0)


def krx_tick(price: float) -> int:
    """The Backend rounds to these. Reproduced here only to prove recovery survives it."""
    for ceiling, tick in (
        (2_000, 1),
        (5_000, 5),
        (20_000, 10),
        (50_000, 50),
        (200_000, 100),
        (500_000, 500),
    ):
        if price < ceiling:
            return tick
    return 1_000


def rounded(price: float) -> float:
    tick = krx_tick(price)
    return round(price / tick) * tick


def test_each_band_sits_either_side_of_the_reference():
    for day, band in enumerate(PRICE_BANDS):
        low, high = reservation_prices(78_000.0, day)

        assert low == pytest.approx(78_000.0 * (1 - band))
        assert high == pytest.approx(78_000.0 * (1 + band))


def test_the_bands_narrow():
    widths = [
        high - low
        for low, high in (reservation_prices(78_000.0, d) for d in range(len(PRICE_BANDS)))
    ]

    assert widths == sorted(widths, reverse=True)


def test_past_the_last_band_there_is_no_pair():
    assert reservation_prices(78_000.0, len(PRICE_BANDS)) is None


@pytest.mark.parametrize("reference", REFERENCES)
def test_a_pair_recovers_its_reference_and_day(reference):
    for day in range(len(PRICE_BANDS)):
        assert read_reservation(*reservation_prices(reference, day)) == (reference, day)


@pytest.mark.parametrize("reference", REFERENCES)
def test_recovery_survives_the_backends_tick_rounding(reference):
    """The Backend rounds both prices before they come back on a pending order, and the
    recovered day still has to be right."""
    for day in range(len(PRICE_BANDS)):
        low, high = reservation_prices(reference, day)

        recovered_reference, recovered_day = read_reservation(rounded(low), rounded(high))

        assert recovered_day == day
        assert recovered_reference == pytest.approx(reference, rel=1e-4)


@pytest.mark.parametrize("reference", REFERENCES)
def test_no_band_rounds_into_a_neighbour(reference):
    """Rounding is the only thing that could make two bands collide, so the recovered
    ratio has to stay nearer its own band than either neighbour."""
    for day, band in enumerate(PRICE_BANDS):
        low, high = (rounded(p) for p in reservation_prices(reference, day))
        ratio = (high - low) / (high + low)

        own = abs(ratio - band)
        others = [abs(ratio - other) for i, other in enumerate(PRICE_BANDS) if i != day]
        assert own < min(others)


def pending(stock_code="005930", order_type="buy", price=74_100.0, amount=100):
    return {
        "order_type": order_type,
        "status": "pending",
        "stock_code": stock_code,
        "price": price,
        "amount": amount,
    }


def test_two_pending_orders_on_the_same_stock_and_side_are_one_pair():
    low, high = reservation_prices(78_000.0, 0)
    orders = [pending(price=low), pending(price=high)]

    pairs = find_pairs(orders)

    assert pairs == {("005930", "buy"): Pair(low=low, high=high, quantity=100)}


def test_a_lone_pending_order_is_not_mistaken_for_a_pair():
    """Half a pair means the other side already filled or was never placed; narrowing it
    would place a rung against a position that has moved."""
    assert find_pairs([pending()]) == {}


def test_the_same_stock_on_opposite_sides_is_not_a_pair():
    orders = [pending(order_type="buy"), pending(order_type="sell")]

    assert find_pairs(orders) == {}


def test_two_stocks_each_make_their_own_pair():
    orders = [
        pending(stock_code="005930", price=74_100.0),
        pending(stock_code="005930", price=81_900.0),
        pending(stock_code="000660", price=391_400.0),
        pending(stock_code="000660", price=432_600.0),
    ]

    pairs = find_pairs(orders)

    assert set(pairs) == {("005930", "buy"), ("000660", "buy")}


def test_an_order_that_is_not_pending_is_ignored():
    low, high = reservation_prices(78_000.0, 0)
    settled = pending(price=high) | {"status": "filled"}

    assert find_pairs([pending(price=low), settled]) == {}


def test_more_than_two_orders_on_one_side_is_not_treated_as_a_pair():
    """Three outstanding orders is a state this service did not create, so it leaves it
    alone rather than guessing which two are the pair."""
    orders = [pending(price=74_100.0), pending(price=81_900.0), pending(price=78_000.0)]

    assert find_pairs(orders) == {}


def test_the_low_comes_back_first_whatever_order_they_arrive_in():
    low, high = reservation_prices(78_000.0, 0)

    forwards = find_pairs([pending(price=low), pending(price=high)])
    backwards = find_pairs([pending(price=high), pending(price=low)])

    assert forwards == backwards == {("005930", "buy"): Pair(low, high, 100)}


def test_the_next_rung_narrows_the_band():
    low, high = reservation_prices(78_000.0, 0)

    rung = next_rung(low, high)

    assert rung == reservation_prices(78_000.0, 1)
    assert rung[1] - rung[0] < high - low


def test_the_rung_after_the_last_band_is_market():
    """Day three ends at market, which is what guarantees the fill."""
    low, high = reservation_prices(78_000.0, len(PRICE_BANDS) - 1)

    assert next_rung(low, high) is None


@pytest.mark.parametrize("reference", REFERENCES)
def test_every_rung_is_reachable_from_the_one_before(reference):
    low, high = reservation_prices(reference, 0)
    seen = [0]
    # Bounded so a rung that stops advancing fails here rather than looping forever.
    for _ in range(len(PRICE_BANDS)):
        rung = next_rung(rounded(low), rounded(high))
        if rung is None:
            break
        low, high = rung
        seen.append(read_reservation(rounded(low), rounded(high))[1])

    assert seen == list(range(len(PRICE_BANDS)))


def test_a_partly_filled_pair_carries_only_what_is_left():
    """Both sides went out with 100. The low side shows 60 outstanding, so 40 already
    filled and 60 is what a narrower rung has to ask for."""
    low, high = reservation_prices(78_000.0, 0)
    orders = [pending(price=low, amount=60), pending(price=high, amount=100)]

    assert find_pairs(orders)[("005930", "buy")].quantity == 60


def test_an_untouched_pair_carries_the_whole_quantity():
    low, high = reservation_prices(78_000.0, 0)
    orders = [pending(price=low, amount=100), pending(price=high, amount=100)]

    assert find_pairs(orders)[("005930", "buy")].quantity == 100
