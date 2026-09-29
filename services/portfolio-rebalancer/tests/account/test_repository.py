import pytest
from portfolio_rebalancer.account import write_poll
from portfolio_rebalancer.database import account_holdings, account_pending_orders, accounts, users

pytestmark = pytest.mark.usefixtures("migrated")


def poll(**changes):
    account = {
        "account_id": 11,
        "account_name": "기본 계좌2",
        "is_ai_managed": True,
        "is_duel_account": False,
        "is_active": True,
        "cash_balance": 1234.00,
        "stocks": [{"stock_code": "005930", "total_price": 12341234, "amount": 123}],
        "pending_orders": [
            {
                "order_type": "buy",
                "status": "pending",
                "stock_code": "005930",
                "price": 1234.00,
                "amount": 123.44,
            }
        ],
    }
    user = {"user_id": 1, "nickname": "스톡스푼", "state": "active", "accounts": [account]}
    user.update(changes)
    return [user]


def rows(conn, table):
    return [dict(row) for row in conn.execute(table.select()).mappings()]


def test_a_poll_writes_the_user_the_account_the_holdings_and_the_pending_orders(conn):
    write_poll(conn, poll())

    assert [row["nickname"] for row in rows(conn, users)] == ["스톡스푼"]
    assert [row["account_id"] for row in rows(conn, accounts)] == [11]
    assert [row["stock_code"] for row in rows(conn, account_holdings)] == ["005930"]
    assert [row["stock_code"] for row in rows(conn, account_pending_orders)] == ["005930"]


def test_a_holdings_renamed_columns_carry_the_payloads_values(conn):
    write_poll(conn, poll())

    holding = rows(conn, account_holdings)[0]
    assert float(holding["quantity"]) == 123.0
    assert float(holding["principal"]) == 12_341_234.0


def test_a_fractional_pending_quantity_is_stored_as_sent(conn):
    """The mirror keeps what the Backend sent; rounding to whole shares belongs to the
    code that decides orders, not to the mirror."""
    write_poll(conn, poll())

    assert float(rows(conn, account_pending_orders)[0]["quantity"]) == 123.44


def test_a_second_poll_updates_the_user_rather_than_duplicating_it(conn):
    write_poll(conn, poll())
    write_poll(conn, poll(nickname="이름바꿈"))

    assert [row["nickname"] for row in rows(conn, users)] == ["이름바꿈"]


def test_a_second_poll_replaces_holdings_rather_than_adding_to_them(conn):
    """A stock sold since the last poll has to disappear, not linger."""
    write_poll(conn, poll())
    later = poll()
    later[0]["accounts"][0]["stocks"] = [{"stock_code": "000660", "total_price": 1, "amount": 2}]

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
    second["stocks"] = [{"stock_code": "035420", "total_price": 5, "amount": 5}]
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


def test_an_accounts_object_is_accepted_as_well_as_a_list(conn):
    one = poll()
    one[0]["accounts"] = one[0]["accounts"][0]

    write_poll(conn, one)

    assert [row["account_id"] for row in rows(conn, accounts)] == [11]
