import pytest
from portfolio_rebalancer.account import write_poll
from portfolio_rebalancer.database import account_holdings, account_pending_orders, accounts, users

pytestmark = pytest.mark.usefixtures("migrated")


def pending(order_id=1, **changes):
    return {
        "order_id": order_id,
        "order_side": "buy",
        "order_type": "limit",
        "order_status": "pending",
        "stock_code": "005930",
        "limit_price": 1234.00,
        "quantity": 123,
        "current_stock_price": 1250.00,
    } | changes


def poll(**changes):
    """GET /api/v1/users/ai-server: a user is its id and its accounts, nothing more."""
    account = {
        "account_id": 11,
        "account_name": "기본 계좌2",
        "is_active": True,
        "cash_balance": 1234.00,
        "stocks": [{"stock_code": "005930", "quantity": 123, "total_cost": 12341234}],
        "pending_orders": [pending()],
    }
    user = {"user_id": 1, "accounts": [account]}
    user.update(changes)
    return [user]


def rows(conn, table):
    return [dict(row) for row in conn.execute(table.select()).mappings()]


def test_a_poll_writes_the_user_the_account_the_holdings_and_the_pending_orders(conn):
    write_poll(conn, poll())

    assert [row["user_id"] for row in rows(conn, users)] == [1]
    assert [row["account_id"] for row in rows(conn, accounts)] == [11]
    assert [row["stock_code"] for row in rows(conn, account_holdings)] == ["005930"]
    assert [row["stock_code"] for row in rows(conn, account_pending_orders)] == ["005930"]


def test_a_holding_carries_the_payloads_quantity_and_cost(conn):
    write_poll(conn, poll())

    holding = rows(conn, account_holdings)[0]
    assert holding["quantity"] == 123
    assert float(holding["total_cost"]) == 12_341_234.0


def test_a_pending_order_keeps_the_backends_id_side_and_quote(conn):
    write_poll(conn, poll())

    order = rows(conn, account_pending_orders)[0]
    assert (order["order_id"], order["order_side"], order["order_type"]) == (1, "buy", "limit")
    assert order["order_status"] == "pending"
    assert (order["quantity"], float(order["limit_price"])) == (123, 1234.0)
    assert float(order["current_stock_price"]) == 1250.0


def test_a_market_order_is_stored_with_no_limit_price(conn):
    market = poll()
    market[0]["accounts"][0]["pending_orders"] = [pending(order_type="market", limit_price=None)]

    write_poll(conn, market)

    assert rows(conn, account_pending_orders)[0]["limit_price"] is None


def test_a_second_poll_updates_the_user_rather_than_duplicating_it(conn):
    write_poll(conn, poll())
    write_poll(conn, poll())

    assert [row["user_id"] for row in rows(conn, users)] == [1]


def test_a_second_poll_replaces_holdings_rather_than_adding_to_them(conn):
    """A stock sold since the last poll has to disappear, not linger."""
    write_poll(conn, poll())
    later = poll()
    later[0]["accounts"][0]["stocks"] = [{"stock_code": "000660", "quantity": 2, "total_cost": 1}]

    write_poll(conn, later)

    assert [row["stock_code"] for row in rows(conn, account_holdings)] == ["000660"]


def test_a_second_poll_replaces_pending_orders_rather_than_adding_to_them(conn):
    write_poll(conn, poll())
    later = poll()
    later[0]["accounts"][0]["pending_orders"] = []

    write_poll(conn, later)

    assert rows(conn, account_pending_orders) == []


def test_an_account_with_no_holdings_or_pending_orders_still_lands(conn):
    empty = poll()
    empty[0]["accounts"][0]["stocks"] = []
    empty[0]["accounts"][0]["pending_orders"] = []

    write_poll(conn, empty)

    assert [row["account_id"] for row in rows(conn, accounts)] == [11]


def test_several_accounts_of_one_user_each_keep_their_own_rows(conn):
    two = poll()
    second = dict(two[0]["accounts"][0])
    second["account_id"] = 12
    second["stocks"] = [{"stock_code": "035420", "quantity": 5, "total_cost": 5}]
    second["pending_orders"] = [pending(order_id=2)]
    two[0]["accounts"] = [two[0]["accounts"][0], second]

    write_poll(conn, two)

    by_account = {row["account_id"]: row["stock_code"] for row in rows(conn, account_holdings)}
    assert by_account == {11: "005930", 12: "035420"}


def test_deleting_an_account_clears_its_holdings_and_pending_orders(conn):
    """The cascades are what let a poll replace a mirror without leaving orphans."""
    write_poll(conn, poll())

    conn.execute(accounts.delete().where(accounts.c.account_id == 11))

    assert rows(conn, account_holdings) == []
    assert rows(conn, account_pending_orders) == []


def test_a_user_with_no_managed_accounts_still_lands(conn):
    """The endpoint lists every user, with an empty list when none of their accounts is
    AI-managed."""
    write_poll(conn, poll(accounts=[]))

    assert [row["user_id"] for row in rows(conn, users)] == [1]
    assert rows(conn, accounts) == []
