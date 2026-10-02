import pytest
from portfolio_rebalancer.rebalance import Quote, ladder, quote, tick

SPREAD = Quote(price=78_000, sma=78_000, sigma=1_950)


def test_quote_takes_the_mean_and_population_deviation():
    result = quote([76_050] * 10 + [79_950] * 10, 78_500)

    assert result == Quote(price=78_500, sma=78_000, sigma=1_950)


def test_a_full_week_buys_at_the_lower_bollinger_band():
    pricing = ladder("buy", SPREAD, 35, 35)

    assert (pricing.order_type, pricing.limit_price, pricing.trigger) == ("limit", 74_100, None)
    assert (pricing.lower_bound, pricing.upper_bound, pricing.alpha) == (74_100, 81_900, 1_950)


def test_a_full_week_sells_at_the_upper_bollinger_band():
    pricing = ladder("sell", SPREAD, 35, 35)

    assert (pricing.order_type, pricing.limit_price) == ("limit", 81_900)


def test_the_bounds_narrow_as_runs_run_out():
    pricing = ladder("buy", SPREAD, 7, 35)

    assert (pricing.lower_bound, pricing.upper_bound) == (77_220, 78_780)
    assert pricing.limit_price == 77_200


def test_a_price_above_the_upper_bound_buys_at_market():
    pricing = ladder("buy", SPREAD.model_copy(update={"price": 82_000}), 35, 35)

    assert (pricing.order_type, pricing.limit_price, pricing.trigger) == ("market", None, "upper")


def test_a_price_below_the_lower_bound_sells_at_market():
    pricing = ladder("sell", SPREAD.model_copy(update={"price": 74_000}), 35, 35)

    assert (pricing.order_type, pricing.limit_price, pricing.trigger) == ("market", None, "lower")


def test_a_price_below_the_lower_bound_still_buys_at_a_limit():
    pricing = ladder("buy", SPREAD.model_copy(update={"price": 74_000}), 35, 35)

    assert (pricing.order_type, pricing.limit_price) == ("limit", 74_100)


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_the_last_run_of_the_week_goes_to_market(side):
    pricing = ladder(side, SPREAD, 1, 35)

    assert (pricing.order_type, pricing.limit_price, pricing.trigger) == (
        "market",
        None,
        "last_run",
    )


def test_limits_round_down_for_buys_and_up_for_sells():
    flat = Quote(price=78_030, sma=78_030, sigma=0)

    assert ladder("buy", flat, 10, 35).limit_price == 78_000
    assert ladder("sell", flat, 10, 35).limit_price == 78_100


def test_a_negative_lower_bound_still_gives_a_positive_buy_limit():
    pricing = ladder("buy", Quote(price=1_000, sma=1_000, sigma=1_000), 35, 35)

    assert pricing.limit_price == 1


@pytest.mark.parametrize(
    ("price", "size"),
    [
        (1_999, 1),
        (2_000, 5),
        (19_990, 10),
        (20_000, 50),
        (199_900, 100),
        (200_000, 500),
        (500_000, 1_000),
    ],
)
def test_tick_follows_the_krx_table(price, size):
    assert tick(price) == size
