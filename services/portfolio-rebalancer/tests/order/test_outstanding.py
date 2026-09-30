from datetime import date, datetime, timedelta, timezone

import pytest
from portfolio_rebalancer.account import AccountState
from portfolio_rebalancer.order.dto import Outstanding
from portfolio_rebalancer.order.outstanding import at_market, narrow, reached_the_backend
from portfolio_rebalancer.order.reservations import (
    PRICE_BANDS,
    limit_and_trigger,
    tick_size,
)
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio

SAMSUNG = ("00126380", "005930")
HYNIX = ("00164779", "000660")
KST = timezone(timedelta(hours=9))

# The 2026-09-28 week has five sessions: Monday 5 days left, Tuesday 4, Wednesday 3,
# Thursday 2, Friday 1.
MONDAY = date(2026, 9, 28)
WEDNESDAY = date(2026, 9, 30)
THURSDAY = date(2026, 10, 1)
FRIDAY = date(2026, 10, 2)
SATURDAY = date(2026, 10, 3)


def noon(day: date) -> datetime:
    """Well before the 15:00 cutoff on the last session."""
    return datetime(day.year, day.month, day.day, 11, 0, tzinfo=KST)


def portfolio(holdings=(), exits=()):
    return Portfolio(portfolio_id=42, cash_weight=0.0, holdings=list(holdings), exits=list(exits))


def holding(pair=SAMSUNG, weight=1.0, reason="사유"):
    return Holding(company_id=pair[0], stock_code=pair[1], weight=weight, reason=reason)


def exited(pair=HYNIX, reason="퇴출 사유"):
    return Exit(company_id=pair[0], stock_code=pair[1], reason=reason)


def account(held=None):
    return AccountState(account_id=11, cash=0.0, held=dict(held or {}))


def working_at(days_left, side="buy", code="005930", reference=78_000.0, quantity=100):
    """An order sitting at the limit that many sessions would have quoted."""
    limit, _ = limit_and_trigger(reference, days_left, side)
    return {(code, side): Outstanding(price=limit, quantity=quantity)}


REFERENCES = {"005930": 78_000.0, "000660": 412_000.0}


def test_an_order_moves_to_the_band_its_remaining_sessions_allow():
    orders = narrow(
        portfolio(holdings=[holding()]),
        account(),
        working_at(5),
        REFERENCES,
        MONDAY,
        noon(THURSDAY),
    )

    assert len(orders) == 1
    assert orders[0].band == PRICE_BANDS[1]
    assert orders[0].limit == limit_and_trigger(78_000.0, 2, "buy")[0]


def test_an_order_already_at_the_right_limit_is_left_alone():
    """The poll runs hourly; re-quoting the same price every hour would be noise."""
    orders = narrow(
        portfolio(holdings=[holding()]),
        account(),
        working_at(2),
        REFERENCES,
        MONDAY,
        noon(THURSDAY),
    )

    assert orders == []


def test_the_deadline_sends_the_order_to_market():
    """Narrowing does not guarantee a fill; the market rung does."""
    past_the_cutoff = datetime(2026, 10, 2, 15, 0, tzinfo=KST)

    orders = narrow(
        portfolio(holdings=[holding()]),
        account(),
        working_at(1),
        REFERENCES,
        MONDAY,
        past_the_cutoff,
    )

    assert len(orders) == 1
    assert (orders[0].limit, orders[0].trigger, orders[0].band) == (None, None, None)
    assert orders[0].note


@pytest.mark.parametrize("shut", [SATURDAY, date(2026, 10, 9)])
def test_nothing_moves_on_a_day_the_exchange_is_shut(shut):
    """Saturday and 한글날 alike: a band spent on a closed market is wasted."""
    orders = narrow(
        portfolio(holdings=[holding()]), account(), working_at(5), REFERENCES, MONDAY, noon(shut)
    )

    assert orders == []


def test_a_missed_session_does_not_hand_the_order_a_day_back():
    """The band comes from the deadline, not a counter."""
    orders = narrow(
        portfolio(holdings=[holding()]),
        account(),
        working_at(5),
        REFERENCES,
        MONDAY,
        noon(FRIDAY),
    )

    assert orders[0].band == PRICE_BANDS[2]


def test_the_reference_comes_from_the_record_not_the_price():
    """One limit price cannot say what it was a band away from, so the record keeps it."""
    orders = narrow(
        portfolio(holdings=[holding()]),
        account(),
        working_at(5),
        {"005930": 100_000.0},
        MONDAY,
        noon(THURSDAY),
    )

    assert orders[0].reference == 100_000.0
    assert orders[0].limit == limit_and_trigger(100_000.0, 2, "buy")[0]


def test_an_order_with_no_recorded_reference_is_left_alone():
    orders = narrow(
        portfolio(holdings=[holding()]), account(), working_at(5), {}, MONDAY, noon(THURSDAY)
    )

    assert orders == []


def test_an_order_the_portfolio_does_not_name_is_left_alone():
    """Someone else placed it."""
    orders = narrow(
        portfolio(holdings=[holding()]),
        account(),
        working_at(5, code="068270"),
        {"068270": 200_000.0},
        MONDAY,
        noon(THURSDAY),
    )

    assert orders == []


def test_the_side_is_preserved():
    orders = narrow(
        portfolio(exits=[exited()]),
        account(),
        working_at(5, side="sell", code="000660", reference=412_000.0),
        REFERENCES,
        MONDAY,
        noon(THURSDAY),
    )

    assert orders[0].action == "sell"
    assert orders[0].limit > 412_000.0


def test_the_quantity_and_the_reason_carry_over():
    orders = narrow(
        portfolio(holdings=[holding(reason="반도체 업황 반등")]),
        account(),
        working_at(5, quantity=73),
        REFERENCES,
        MONDAY,
        noon(THURSDAY),
    )

    assert orders[0].shares == 73
    assert orders[0].reason == "반도체 업황 반등"
    assert orders[0].account_id == 11


def test_every_order_advances_on_the_one_shared_deadline():
    both = working_at(5) | working_at(5, side="sell", code="000660", reference=412_000.0)

    orders = narrow(
        portfolio(holdings=[holding()], exits=[exited()]),
        account(),
        both,
        REFERENCES,
        MONDAY,
        noon(THURSDAY),
    )

    assert sorted(order.stock_code for order in orders) == ["000660", "005930"]
    assert {order.band for order in orders} == {PRICE_BANDS[1]}


def test_nothing_working_means_no_orders():
    assert (
        narrow(portfolio(holdings=[holding()]), account(), {}, REFERENCES, MONDAY, noon(WEDNESDAY))
        == []
    )


@pytest.mark.parametrize("reference", [1_999.0, 49_999.0, 499_999.0])
def test_a_re_quoted_order_lands_on_a_tick(reference):
    """Every rung goes to the Backend, not just the first."""
    orders = narrow(
        portfolio(holdings=[holding()]),
        account(),
        working_at(5, reference=reference),
        {"005930": reference},
        MONDAY,
        noon(THURSDAY),
    )

    order = orders[0]
    for price in (order.limit, order.trigger, order.reference):
        assert price % tick_size(price) == 0, f"{reference} → {price}"


# ---- at_market ----


def test_a_struck_order_carries_no_limit_and_no_trigger():
    """Crossing the trigger is the signal that waiting has stopped being worth it."""
    order = at_market(account(), holding(), "005930", "buy", 73, 78_000.0)

    assert (order.limit, order.trigger, order.band) == (None, None, None)
    assert order.shares == 73
    assert order.note


def test_a_struck_order_keeps_its_reason_and_reference():
    order = at_market(account(), holding(reason="반도체"), "005930", "buy", 10, 78_000.0)

    assert order.reason == "반도체"
    assert order.reference == 78_000.0


# ---- reached_the_backend ----


def test_an_order_still_working_means_the_request_arrived():
    """Every placeable order goes out in one request, so one of them working is proof."""
    assert reached_the_backend(["005930", "000660"], working_at(5)) is True


def test_nothing_working_means_the_request_never_arrived():
    assert reached_the_backend(["005930"], {}) is False


def test_someone_elses_order_is_not_proof():
    assert reached_the_backend(["005930"], working_at(5, code="068270")) is False
