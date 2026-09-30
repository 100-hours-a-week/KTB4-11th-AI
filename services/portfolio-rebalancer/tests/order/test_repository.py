import pytest
import sqlalchemy as sa
from portfolio_rebalancer.database import rebalance_orders
from portfolio_rebalancer.order import (
    Order,
    amend_orders,
    find_orders,
    mark_sent,
    record_orders,
)

pytestmark = pytest.mark.usefixtures("migrated")


def order(**changes):
    values = {
        "account_id": 11,
        "company_id": "00126380",
        "stock_code": "005930",
        "action": "buy",
        "shares": 10,
        "reason": "반도체 업황 반등",
        "reference": 78_000.0,
        "limit": 74_100.0,
        "trigger": 81_900.0,
    }
    return Order(**(values | changes))


def rows(conn, table):
    return [dict(row) for row in conn.execute(table.select()).mappings()]


def test_orders_are_recorded_unsent(conn, portfolio_id):
    """Recorded before anything goes out, so a crash between the two leaves a record
    rather than a silent order."""
    record_orders(conn, portfolio_id, [order()])

    recorded = rows(conn, rebalance_orders)[0]
    assert recorded["sent_at"] is None
    assert recorded["stock_code"] == "005930"
    assert float(recorded["limit_price"]) == 74_100.0
    assert float(recorded["trigger_price"]) == 81_900.0
    assert recorded["reason"] == "반도체 업황 반등"


def test_a_skip_records_without_a_limit_or_a_trigger(conn, portfolio_id):
    record_orders(
        conn,
        portfolio_id,
        [order(action="skip", shares=0, reference=None, limit=None, trigger=None)],
    )

    recorded = rows(conn, rebalance_orders)[0]
    assert (recorded["limit_price"], recorded["trigger_price"]) == (None, None)


def test_marking_sent_stamps_the_recorded_orders(conn, portfolio_id):
    record_orders(conn, portfolio_id, [order()])

    mark_sent(conn, portfolio_id, account_id=11)

    assert rows(conn, rebalance_orders)[0]["sent_at"] is not None


def test_marking_sent_leaves_another_account_alone(conn, portfolio_id):
    record_orders(conn, portfolio_id, [order(account_id=11), order(account_id=12)])

    mark_sent(conn, portfolio_id, account_id=11)

    sent = {row["account_id"]: row["sent_at"] is not None for row in rows(conn, rebalance_orders)}
    assert sent == {11: True, 12: False}


def test_find_orders_returns_what_a_repeated_rebalance_replies_with(conn, portfolio_id):
    record_orders(conn, portfolio_id, [order(stock_code="005930"), order(stock_code="000660")])

    got = find_orders(conn, portfolio_id, account_id=11)

    assert [row["stock_code"] for row in got] == ["005930", "000660"]


def test_find_orders_is_empty_for_an_account_never_rebalanced(conn, portfolio_id):
    assert find_orders(conn, portfolio_id, account_id=99) == []


def test_the_same_portfolio_account_and_stock_cannot_be_recorded_twice(conn, portfolio_id):
    """This constraint is what makes a second rebalance a no-op instead of a double buy."""
    record_orders(conn, portfolio_id, [order()])

    with pytest.raises(sa.exc.IntegrityError):
        record_orders(conn, portfolio_id, [order()])


def test_recording_nothing_writes_nothing(conn, portfolio_id):
    record_orders(conn, portfolio_id, [])

    assert rows(conn, rebalance_orders) == []


def test_a_recorded_pair_is_marked_reserved(conn, portfolio_id):
    """side says buy or sell; status says which rung it sits on."""
    record_orders(conn, portfolio_id, [order()])

    assert rows(conn, rebalance_orders)[0]["status"] == "reserved"


def test_a_market_order_is_marked_market(conn, portfolio_id):
    record_orders(conn, portfolio_id, [order(limit=None, trigger=None)])

    assert rows(conn, rebalance_orders)[0]["status"] == "market"


def test_a_skip_is_marked_skip(conn, portfolio_id):
    record_orders(conn, portfolio_id, [order(action="skip", shares=0, limit=None, trigger=None)])

    assert rows(conn, rebalance_orders)[0]["status"] == "skip"


def test_amending_replaces_the_pair_in_place(conn, portfolio_id):
    """A narrowing is one amended order. The unique constraint forbids a second row, so
    an insert here would raise instead."""
    record_orders(conn, portfolio_id, [order()])

    amend_orders(conn, portfolio_id, [order(limit=75_700.0, trigger=80_300.0)])

    recorded = rows(conn, rebalance_orders)
    assert len(recorded) == 1
    assert (float(recorded[0]["limit_price"]), float(recorded[0]["trigger_price"])) == (
        75_700.0,
        80_300.0,
    )


def test_amending_puts_the_order_back_to_unsent(conn, portfolio_id):
    """The amendment is recorded before it is sent, exactly as the first rung was."""
    record_orders(conn, portfolio_id, [order()])
    mark_sent(conn, portfolio_id, account_id=11)

    amend_orders(conn, portfolio_id, [order(limit=75_700.0, trigger=80_300.0)])

    assert rows(conn, rebalance_orders)[0]["sent_at"] is None


def test_amending_to_market_clears_the_pair(conn, portfolio_id):
    record_orders(conn, portfolio_id, [order()])

    amend_orders(conn, portfolio_id, [order(limit=None, trigger=None)])

    recorded = rows(conn, rebalance_orders)[0]
    assert (recorded["limit_price"], recorded["trigger_price"]) == (None, None)
    assert recorded["status"] == "market"


def test_amending_carries_the_outstanding_quantity(conn, portfolio_id):
    record_orders(conn, portfolio_id, [order(shares=100)])

    amend_orders(conn, portfolio_id, [order(shares=60, limit=75_700.0, trigger=80_300.0)])

    assert float(rows(conn, rebalance_orders)[0]["quantity"]) == 60.0


def test_amending_leaves_another_account_alone(conn, portfolio_id):
    record_orders(conn, portfolio_id, [order(account_id=11), order(account_id=12)])

    amend_orders(conn, portfolio_id, [order(account_id=11, limit=1.0, trigger=2.0)])

    untouched = next(r for r in rows(conn, rebalance_orders) if r["account_id"] == 12)
    assert float(untouched["limit_price"]) == 74_100.0
