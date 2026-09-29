from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from portfolio_rebalancer.decide.accounts import AccountState
from portfolio_rebalancer.decide.outstanding import narrow
from portfolio_rebalancer.decide.reservations import PRICE_BANDS, Pair, reservation_prices
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio

SAMSUNG = ("00126380", "005930")
HYNIX = ("00164779", "000660")

# The 2026-09-28 week has five sessions, so a Monday cycle has five days: Monday 5,
# Tuesday 4, Wednesday 3, Thursday 2, Friday 1.
MONDAY = date(2026, 9, 28)
TUESDAY = date(2026, 9, 29)
THURSDAY = date(2026, 10, 1)
FRIDAY = date(2026, 10, 2)
SATURDAY = date(2026, 10, 3)
SUNDAY = date(2026, 10, 4)


KST = timezone(timedelta(hours=9))


def noon(day):
    """A KST datetime in the middle of the session, well before the 14:30 cutoff."""
    return datetime(day.year, day.month, day.day, 11, 0, tzinfo=KST)


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


def test_a_pair_narrows_as_the_week_runs_down():
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(THURSDAY))

    assert len(orders) == 1
    assert orders[0].band == PRICE_BANDS[1]
    assert (orders[0].low, orders[0].high) == reservation_prices(78_000.0, 1)


def test_a_pair_sent_today_is_left_alone():
    """The rung advances once per trading day, and the poll runs hourly."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, TUESDAY, noon(TUESDAY))

    assert orders == []


@pytest.mark.parametrize("weekend", [SATURDAY, SUNDAY])
def test_nothing_narrows_when_the_market_is_shut(weekend):
    """A rung spent on a closed market is a rung wasted, and there are only three."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(weekend))

    assert orders == []


def test_the_band_tracks_the_sessions_left_not_the_calendar_days():
    """Monday to Friday is four calendar days but one session left, which is the last
    band."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(FRIDAY))

    assert len(orders) == 1
    assert orders[0].band == PRICE_BANDS[2]


def test_the_deadline_falls_through_to_market():
    """Narrowing does not guarantee a fill; the market rung does."""
    pairs = {("005930", "buy"): pair_for(day=len(PRICE_BANDS) - 1)}
    past_the_cutoff = datetime(2026, 10, 2, 15, 0, tzinfo=KST)

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, past_the_cutoff)

    assert len(orders) == 1
    assert (orders[0].low, orders[0].high, orders[0].band) == (None, None, None)
    assert orders[0].note


def test_the_quantity_carries_over_unchanged():
    pairs = {("005930", "buy"): pair_for(day=0, quantity=73)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(THURSDAY))

    assert orders[0].shares == 73


def test_a_partly_filled_pair_narrows_only_what_is_left():
    pairs = {("005930", "buy"): pair_for(day=0, quantity=60)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(THURSDAY))

    assert orders[0].shares == 60


def test_the_reference_is_the_one_the_first_pair_fixed():
    """Every rung is measured from the original reference, not from wherever the price
    has walked to since."""
    pairs = {("005930", "buy"): pair_for(day=0, reference=78_000.0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(THURSDAY))

    assert orders[0].reference == pytest.approx(78_000.0)


def test_the_side_is_preserved():
    pairs = {("000660", "sell"): pair_for(day=0, reference=412_000.0)}

    orders = narrow(portfolio(exits=[exited()]), account(), pairs, MONDAY, noon(THURSDAY))

    assert orders[0].action == "sell"


def test_the_reason_still_travels_with_the_order():
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(
        portfolio(holdings=[holding(reason="반도체 업황 반등")]),
        account(),
        pairs,
        MONDAY,
        noon(THURSDAY),
    )

    assert orders[0].reason == "반도체 업황 반등"
    assert orders[0].account_id == 11


def test_a_pair_the_portfolio_does_not_name_is_left_alone():
    """Someone else placed it, so this service has no reason to move it."""
    pairs = {("068270", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(THURSDAY))

    assert orders == []


def test_a_pair_with_no_recorded_send_is_left_alone():
    """Without a send time there is no way to know a trading day has passed."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, TUESDAY, noon(TUESDAY))

    assert orders == []


def test_an_unsent_record_is_left_alone():
    """sent_at is null between recording and sending, so the pair is not on the market
    yet and nothing about it has aged."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, TUESDAY, noon(TUESDAY))

    assert orders == []


def test_every_pair_advances_on_the_one_shared_deadline():
    """Selling and buying share three trading days, so the pairs move together rather than
    each keeping its own count."""
    pairs = {
        ("005930", "buy"): pair_for(day=0),
        ("000660", "sell"): pair_for(day=0, reference=412_000.0),
    }

    orders = narrow(
        portfolio(holdings=[holding()], exits=[exited()]),
        account(),
        pairs,
        MONDAY,
        noon(THURSDAY),
    )

    assert sorted(order.stock_code for order in orders) == ["000660", "005930"]
    assert {order.band for order in orders} == {PRICE_BANDS[1]}


def test_no_pairs_means_no_orders():
    assert narrow(portfolio(holdings=[holding()]), account(), {}, TUESDAY, noon(TUESDAY)) == []


def test_a_pair_already_at_the_right_band_is_left_alone():
    """The poll runs hourly, so re-quoting the same band every hour would be noise."""
    pairs = {("005930", "buy"): pair_for(day=1)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(THURSDAY))

    assert orders == []


def test_a_missed_day_does_not_hand_the_order_a_day_back():
    """The band comes from the deadline, not a counter, so a tick the service missed
    cannot leave an order wider than its remaining days allow."""
    pairs = {("005930", "buy"): pair_for(day=0)}

    orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(FRIDAY))

    assert orders[0].band == PRICE_BANDS[2]


def test_a_narrowed_pair_also_lands_on_a_tick():
    """Every rung goes to the Backend, not just the first, so every rung has to be
    quotable."""
    from portfolio_rebalancer.decide.reservations import tick_size

    for reference in (1_999.0, 49_999.0, 499_999.0):
        pairs = {("005930", "buy"): pair_for(day=0, reference=reference)}

        orders = narrow(portfolio(holdings=[holding()]), account(), pairs, MONDAY, noon(THURSDAY))

        order = orders[0]
        for price in (order.low, order.high, order.reference):
            assert price % tick_size(price) == 0, f"{reference} → {price}"
