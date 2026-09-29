import pytest
from portfolio_rebalancer.decide.reservations import (
    PRICE_BANDS,
    TICK_SIZES,
    Pair,
    band_for,
    days_left_of,
    find_pairs,
    next_rung,
    on_tick,
    prices_for,
    read_reservation,
    reservation_prices,
    tick_size,
)

REFERENCES = (1_234.0, 9_050.0, 78_000.0, 155_500.0, 412_000.0, 1_800_000.0)
# Every tick boundary and the price either side of it.
BOUNDARIES = tuple(
    price
    for ceiling, _ in TICK_SIZES
    for price in (float(ceiling - 1), float(ceiling), float(ceiling + 1))
)


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


def test_both_prices_of_a_pair_land_on_a_tick():
    """An off-tick price is a 400 from the Backend, not a rounding it does for us."""
    for day in range(len(PRICE_BANDS)):
        for reference in (*REFERENCES, *BOUNDARIES):
            low, high = reservation_prices(reference, day)

            assert low % tick_size(low) == 0
            assert high % tick_size(high) == 0


def test_each_band_sits_either_side_of_the_reference():
    """Within one tick of the band, since the price has to land on a tick."""
    for day, band in enumerate(PRICE_BANDS):
        low, high = reservation_prices(78_000.0, day)

        assert low == pytest.approx(78_000.0 * (1 - band), abs=tick_size(low))
        assert high == pytest.approx(78_000.0 * (1 + band), abs=tick_size(high))
        assert low < 78_000.0 < high


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

        recovered_reference, recovered_day = read_reservation(low, high)

        assert recovered_day == day
        assert recovered_reference == pytest.approx(reference, rel=1e-4)


@pytest.mark.parametrize("reference", REFERENCES)
def test_no_band_rounds_into_a_neighbour(reference):
    """Rounding is the only thing that could make two bands collide, so the recovered
    ratio has to stay nearer its own band than either neighbour."""
    for day, band in enumerate(PRICE_BANDS):
        low, high = reservation_prices(reference, day)
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
        rung = next_rung(low, high)
        if rung is None:
            break
        low, high = rung
        seen.append(read_reservation(low, high)[1])

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


@pytest.mark.parametrize("reference", BOUNDARIES)
def test_a_pair_astride_a_tick_boundary_still_recovers_its_day(reference):
    """The risky case: the low falls in one tick band and the high in the next, so the two
    are rounded by different amounts and the ratio is no longer exactly the band."""
    for day in range(len(PRICE_BANDS)):
        low, high = reservation_prices(reference, day)

        recovered_reference, recovered_day = read_reservation(low, high)

        assert recovered_day == day
        assert recovered_reference == pytest.approx(reference, rel=0.002)


def test_some_boundary_pair_really_does_straddle_two_tick_bands():
    """Guards the test above from passing because the case never arises."""
    straddling = [
        reference
        for reference in BOUNDARIES
        for day in range(len(PRICE_BANDS))
        if tick_size(reservation_prices(reference, day)[0])
        != tick_size(reservation_prices(reference, day)[1])
    ]

    assert straddling


LADDER_DAYS = len(PRICE_BANDS)


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [(3, PRICE_BANDS[0]), (2, PRICE_BANDS[1]), (1, PRICE_BANDS[2])],
)
def test_the_band_comes_from_the_days_left_not_the_days_spent(days_left, expected):
    """Selling and buying share one three-day deadline, so an order that starts late
    starts on a narrower band rather than restarting the ladder."""
    assert band_for(days_left) == expected


@pytest.mark.parametrize("days_left", [0, -1, -5])
def test_no_days_left_means_market(days_left):
    """The market rung is what guarantees the fill, so a deadline reached goes to market
    rather than placing a reservation that may not fill."""
    assert band_for(days_left) is None


def test_more_days_than_the_ladder_has_is_the_widest_band():
    assert band_for(LADDER_DAYS + 5) == PRICE_BANDS[0]


def test_prices_for_days_left_match_the_band():
    low, high = prices_for(78_000.0, days_left=2)

    assert (low, high) == reservation_prices(78_000.0, 1)


def test_no_days_left_has_no_prices():
    assert prices_for(78_000.0, days_left=0) is None


def test_a_pair_says_how_many_days_it_has_left():
    """Reading the pair back is how the deadline survives without being stored."""
    for days_left in range(1, LADDER_DAYS + 1):
        low, high = prices_for(78_000.0, days_left)

        assert days_left_of(low, high) == days_left


def test_two_unrelated_orders_on_one_stock_are_not_a_pair():
    """A reservation's two prices sit either side of a reference at one of the bands.
    Two orders at 1,000 and 2,000 are 33% apart and are simply two orders."""
    orders = [pending(price=1_000.0, amount=10), pending(price=2_000.0, amount=5)]

    assert find_pairs(orders) == {}


@pytest.mark.parametrize("reference", REFERENCES)
def test_every_band_is_still_recognised_after_rounding(reference):
    """The tolerance must not be so tight that a real pair stops being one."""
    for days in range(1, len(PRICE_BANDS) + 1):
        low, high = prices_for(reference, days)
        orders = [pending(price=low, amount=10), pending(price=high, amount=10)]

        assert ("005930", "buy") in find_pairs(orders)


def test_a_partly_filled_pair_is_still_recognised():
    """Quantities stop matching after a partial fill, so they cannot be what identifies
    a pair -- the prices are."""
    low, high = prices_for(78_000.0, 3)
    orders = [pending(price=low, amount=6), pending(price=high, amount=15)]

    assert find_pairs(orders)[("005930", "buy")].quantity == 6
