"""Store tests run against a real PostgreSQL, because what is being checked is what the
database does: cascades, a unique constraint, and replace-not-append."""

import pytest
import sqlalchemy as sa
from portfolio_rebalancer.order import Order
from portfolio_rebalancer.request.store import (
    account_holdings,
    account_pending_orders,
    accounts,
    amend_orders,
    mark_sent,
    rebalance_orders,
    record_orders,
    save_poll,
    stored_orders,
    users,
)

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


def test_a_poll_writes_the_user_the_account_the_holdings_and_the_pending_orders(conn):
    save_poll(conn, poll())

    assert [row["nickname"] for row in rows(conn, users)] == ["스톡스푼"]
    assert [row["account_id"] for row in rows(conn, accounts)] == [11]
    assert [row["stock_code"] for row in rows(conn, account_holdings)] == ["005930"]
    assert [row["stock_code"] for row in rows(conn, account_pending_orders)] == ["005930"]


def test_a_holdings_renamed_columns_carry_the_payloads_values(conn):
    save_poll(conn, poll())

    holding = rows(conn, account_holdings)[0]
    assert float(holding["quantity"]) == 123.0
    assert float(holding["principal"]) == 12_341_234.0


def test_a_fractional_pending_quantity_is_stored_as_sent(conn):
    """The mirror keeps what the Backend sent; rounding to whole shares belongs to the
    code that decides orders, not to the mirror."""
    save_poll(conn, poll())

    assert float(rows(conn, account_pending_orders)[0]["quantity"]) == 123.44


def test_a_second_poll_updates_the_user_rather_than_duplicating_it(conn):
    save_poll(conn, poll())
    save_poll(conn, poll(nickname="이름바꿈"))

    assert [row["nickname"] for row in rows(conn, users)] == ["이름바꿈"]


def test_a_second_poll_replaces_holdings_rather_than_adding_to_them(conn):
    """A stock sold since the last poll has to disappear, not linger."""
    save_poll(conn, poll())
    later = poll()
    later[0]["accounts"][0]["stocks"] = [{"stock_code": "000660", "total_price": 1, "amount": 2}]

    save_poll(conn, later)

    assert [row["stock_code"] for row in rows(conn, account_holdings)] == ["000660"]


def test_a_second_poll_replaces_pending_orders_rather_than_adding_to_them(conn):
    save_poll(conn, poll())
    later = poll()
    later[0]["accounts"][0]["pending_orders"] = []

    save_poll(conn, later)

    assert rows(conn, account_pending_orders) == []


def test_an_account_with_no_holdings_or_pending_orders_still_lands(conn):
    empty = poll()
    empty[0]["accounts"][0]["stocks"] = []
    empty[0]["accounts"][0]["pending_orders"] = []

    save_poll(conn, empty)

    assert [row["account_id"] for row in rows(conn, accounts)] == [11]


def test_several_accounts_of_one_user_each_keep_their_own_rows(conn):
    two = poll()
    second = dict(two[0]["accounts"][0])
    second["account_id"] = 12
    second["stocks"] = [{"stock_code": "035420", "total_price": 5, "amount": 5}]
    two[0]["accounts"] = [two[0]["accounts"][0], second]

    save_poll(conn, two)

    by_account = {row["account_id"]: row["stock_code"] for row in rows(conn, account_holdings)}
    assert by_account == {11: "005930", 12: "035420"}


def test_deleting_an_account_clears_its_holdings_and_pending_orders(conn):
    """The cascades are what let a poll replace a mirror without leaving orphans."""
    save_poll(conn, poll())

    conn.execute(accounts.delete().where(accounts.c.account_id == 11))

    assert rows(conn, account_holdings) == []
    assert rows(conn, account_pending_orders) == []


def test_an_accounts_object_is_accepted_as_well_as_a_list(conn):
    one = poll()
    one[0]["accounts"] = one[0]["accounts"][0]

    save_poll(conn, one)

    assert [row["account_id"] for row in rows(conn, accounts)] == [11]


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


def test_stored_orders_returns_what_a_repeated_rebalance_replies_with(conn, portfolio_id):
    record_orders(conn, portfolio_id, [order(stock_code="005930"), order(stock_code="000660")])

    got = stored_orders(conn, portfolio_id, account_id=11)

    assert [row["stock_code"] for row in got] == ["005930", "000660"]


def test_stored_orders_is_empty_for_an_account_never_rebalanced(conn, portfolio_id):
    assert stored_orders(conn, portfolio_id, account_id=99) == []


def test_the_same_portfolio_account_and_stock_cannot_be_recorded_twice(conn, portfolio_id):
    """This constraint is what makes a second rebalance a no-op instead of a double buy."""
    record_orders(conn, portfolio_id, [order()])

    with pytest.raises(sa.exc.IntegrityError):
        record_orders(conn, portfolio_id, [order()])


def test_recording_nothing_writes_nothing(conn, portfolio_id):
    record_orders(conn, portfolio_id, [])

    assert rows(conn, rebalance_orders) == []


def test_no_portfolio_reads_as_none_rather_than_an_empty_one(conn):
    """An empty Portfolio would look like "sell everything"; None means "nothing decided yet"."""
    from portfolio_rebalancer.request.store import latest_portfolio

    assert latest_portfolio(conn) is None


def test_the_newest_portfolio_wins(conn):
    from portfolio_rebalancer.request.store import latest_portfolio

    older, newer = (
        conn.execute(
            sa.text(
                "INSERT INTO portfolios (created_at, cash_weight, commentary, model)"
                f" VALUES ('{when}', {weight}, '', 'test') RETURNING id"
            )
        ).scalar()
        for when, weight in (("2026-09-20T00:00:00Z", 0.1), ("2026-09-27T00:00:00Z", 0.2))
    )

    portfolio = latest_portfolio(conn)

    assert portfolio.portfolio_id == newer != older
    assert portfolio.cash_weight == 0.2


def test_a_holding_carries_the_stock_code_joined_in_from_companies(conn, portfolio_id):
    """company_id is DART's corp_code, which no exchange accepts as an order identifier."""
    from portfolio_rebalancer.request.store import latest_portfolio

    conn.execute(
        sa.text(
            "INSERT INTO companies (corp_code, stock_code, corp_name)"
            " VALUES ('00126380', '005930', '삼성전자')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight, reason)"
            " VALUES (:pid, '00126380', 0.3, '반도체 업황 반등')"
        ),
        {"pid": portfolio_id},
    )

    holding = latest_portfolio(conn).holdings[0]

    assert (holding.company_id, holding.stock_code) == ("00126380", "005930")
    assert holding.weight == 0.3
    assert holding.reason == "반도체 업황 반등"


def test_a_holding_with_no_reason_upstream_carries_none(conn, portfolio_id):
    from portfolio_rebalancer.request.store import latest_portfolio

    conn.execute(
        sa.text(
            "INSERT INTO companies (corp_code, stock_code, corp_name)"
            " VALUES ('00126380', '005930', '삼성전자')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
            " VALUES (:pid, '00126380', 0.3)"
        ),
        {"pid": portfolio_id},
    )

    assert latest_portfolio(conn).holdings[0].reason is None


def test_an_exit_carries_its_reason(conn, portfolio_id):
    from portfolio_rebalancer.request.store import latest_portfolio

    conn.execute(
        sa.text(
            "INSERT INTO companies (corp_code, stock_code, corp_name)"
            " VALUES ('00164779', '000660', 'SK하이닉스')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
            " VALUES (:pid, '00164779', '비중 축소')"
        ),
        {"pid": portfolio_id},
    )

    left = latest_portfolio(conn).exits[0]

    assert (left.stock_code, left.reason) == ("000660", "비중 축소")


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
