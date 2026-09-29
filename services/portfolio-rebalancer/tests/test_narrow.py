from datetime import UTC, date, datetime, timedelta

import pytest
from portfolio_rebalancer.accounts import AccountState
from portfolio_rebalancer.rebalance import Exit, Holding, Portfolio, narrow
from portfolio_rebalancer.reservations import PRICE_BANDS, Pair, reservation_prices

SAMSUNG = ("00126380", "005930")
HYNIX = ("00164779", "000660")

MONDAY = date(2026, 9, 28)
TUESDAY = date(2026, 9, 29)
SATURDAY = date(2026, 10, 3)
SUNDAY = date(2026, 10, 4)


def portfolio(holdings=(), exits=()):
    return Portfolio(portfolio_id=42, cash_weight=0.0, holdings=list(holdings), exits=list(exits))


def holding(pair=SAMSUNG, weight=1.0, reason="사유"):
    return Holding(company_id=pair[0], stock_code=pair[1], weight=weight, reason=reason)


def exited(pair=HYNIX, reason="퇴출 사유"):
    return Exit(company_id=pair[0], stock_code=pair[1], reason=reason)


def account(held=None):
    return AccountState(account_id=11, cash=0.0, held=dict(held or {}))


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)


def pair_for(day=0, reference=78_000.0, quantity=100):
    low, high = reservation_prices(reference, day)
    return Pair(low=low, high=high, quantity=quantity)


def test_a_pair_sent_yesterday_narrows_one_rung():
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"005930": at(MONDAY)}, TUESDAY
    )

    assert len(orders) == 1
    assert orders[0].band == PRICE_BANDS[1]
    assert (orders[0].low, orders[0].high) == reservation_prices(78_000.0, 1)


def test_a_pair_sent_today_is_left_alone():
    """The rung advances once per trading day, and the poll runs hourly."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"005930": at(TUESDAY)}, TUESDAY
    )

    assert orders == []


@pytest.mark.parametrize("weekend", [SATURDAY, SUNDAY])
def test_nothing_narrows_when_the_market_is_shut(weekend):
    """A rung spent on a closed market is a rung wasted, and there are only three."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"005930": at(MONDAY)}, weekend
    )

    assert orders == []


def test_a_pair_over_a_weekend_narrows_once():
    friday = date(2026, 10, 2)
    monday = date(2026, 10, 5)
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"005930": at(friday)}, monday
    )

    assert len(orders) == 1
    assert orders[0].band == PRICE_BANDS[1]


def test_the_last_band_falls_through_to_market():
    """Narrowing does not guarantee a fill; the market rung does."""
    pairs = {("005930", "buy"): pair_for(day=len(PRICE_BANDS) - 1)}

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"005930": at(MONDAY)}, TUESDAY
    )

    assert len(orders) == 1
    assert (orders[0].low, orders[0].high, orders[0].band) == (None, None, None)
    assert orders[0].note


def test_the_quantity_carries_over_unchanged():
    pairs = {("005930", "buy"): pair_for(day=0, quantity=73)}

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"005930": at(MONDAY)}, TUESDAY
    )

    assert orders[0].shares == 73


def test_a_partly_filled_pair_narrows_only_what_is_left():
    pairs = {("005930", "buy"): pair_for(day=0, quantity=60)}

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"005930": at(MONDAY)}, TUESDAY
    )

    assert orders[0].shares == 60


def test_the_reference_is_the_one_the_first_pair_fixed():
    """Every rung is measured from the original reference, not from wherever the price
    has walked to since."""
    pairs = {("005930", "buy"): pair_for(day=0, reference=78_000.0)}

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"005930": at(MONDAY)}, TUESDAY
    )

    assert orders[0].reference == pytest.approx(78_000.0)


def test_the_side_is_preserved():
    pairs = {("000660", "sell"): pair_for(day=0, reference=412_000.0)}

    orders = narrow(portfolio(exits=[exited()]), account(), pairs, {"000660": at(MONDAY)}, TUESDAY)

    assert orders[0].action == "sell"


def test_the_reason_still_travels_with_the_order():
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(
        portfolio(holdings=[holding(reason="반도체 업황 반등")]),
        account(),
        pairs,
        {"005930": at(MONDAY)},
        TUESDAY,
    )

    assert orders[0].reason == "반도체 업황 반등"
    assert orders[0].account_id == 11


def test_a_pair_the_portfolio_does_not_name_is_left_alone():
    """Someone else placed it, so this service has no reason to move it."""
    pairs = {("068270", "buy"): pair_for(day=0)}

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"068270": at(MONDAY)}, TUESDAY
    )

    assert orders == []


def test_a_pair_with_no_recorded_send_is_left_alone():
    """Without a send time there is no way to know a trading day has passed."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, {}, TUESDAY)

    assert orders == []


def test_an_unsent_record_is_left_alone():
    """sent_at is null between recording and sending, so the pair is not on the market
    yet and nothing about it has aged."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, {"005930": None}, TUESDAY)

    assert orders == []


def test_several_pairs_each_advance_on_their_own_clock():
    pairs = {
        ("005930", "buy"): pair_for(day=0),
        ("000660", "sell"): pair_for(day=0, reference=412_000.0),
    }
    last_sent = {"005930": at(MONDAY), "000660": at(TUESDAY)}

    orders = narrow(
        portfolio(holdings=[holding()], exits=[exited()]), account(), pairs, last_sent, TUESDAY
    )

    assert [order.stock_code for order in orders] == ["005930"]


def test_no_pairs_means_no_orders():
    assert narrow(portfolio(holdings=[holding()]), account(), {}, {}, TUESDAY) == []


def test_a_send_time_in_the_future_is_left_alone():
    """Clock skew must not advance a rung early."""
    pairs = {("005930", "buy"): pair_for(day=0)}
    tomorrow = at(TUESDAY) + timedelta(days=1)

    orders = narrow(
        portfolio(holdings=[holding()]), account(), pairs, {"005930": tomorrow}, TUESDAY
    )

    assert orders == []
