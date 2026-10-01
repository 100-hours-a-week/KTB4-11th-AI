"""What `GET /api/v1/users/ai-server` sends, folded into what an account can spend.

The payload names here are the Backend's own: `quantity` rather than `amount`,
`total_cost` rather than `total_price`, and `order_side` for buy or sell, which is a
different field from `order_type`, where the Backend puts limit or market.
"""

from portfolio_rebalancer.account import (
    apply_pending,
    managed_accounts,
)


def account(**changes):
    values = {
        "account_id": 11,
        "account_name": "기본 계좌2",
        "is_active": True,
        "cash_balance": 10_000_000,
        "stocks": [{"stock_code": "005930", "total_cost": 7_800_000, "quantity": 100}],
        "pending_orders": [],
    }
    return values | changes


def user(**changes):
    return {"user_id": 1, "accounts": [account()]} | changes


def order(**changes):
    values = {
        "order_id": 1,
        "stock_code": "005930",
        "order_side": "buy",
        "order_status": "pending",
        "order_type": "limit",
        "limit_price": 78_000,
        "quantity": 10,
        "current_stock_price": 78_000,
    }
    return values | changes


def test_cash_and_holdings_come_through_when_nothing_is_pending():
    state = apply_pending(account(), {})

    assert state.account_id == 11
    assert state.cash == 10_000_000.0
    assert state.held == {"005930": 100}


def test_a_pending_buy_is_not_cash_this_service_may_spend():
    state = apply_pending(account(pending_orders=[order(limit_price=78_000, quantity=10)]), {})

    assert state.cash == 10_000_000.0 - 780_000.0


def test_a_pending_sell_does_not_add_cash_before_it_fills():
    """Proceeds are not spendable until the order actually fills."""
    state = apply_pending(account(pending_orders=[order(order_side="sell", quantity=10)]), {})

    assert state.cash == 10_000_000.0


def test_a_market_buy_is_costed_at_the_close_since_it_carries_no_limit():
    """`limit_price` is null on a market order, and treating that as free would let the
    buys spend money the order has already committed."""
    pending = [order(order_type="market", limit_price=None)]

    state = apply_pending(account(pending_orders=pending), {"005930": 80_000.0})

    assert state.cash == 10_000_000.0 - 10 * 80_000.0


def test_a_pending_buy_raises_the_quantity_the_account_is_on_its_way_to_holding():
    state = apply_pending(account(pending_orders=[order(quantity=10)]), {})

    assert state.held["005930"] == 110


def test_a_pending_sell_lowers_it():
    state = apply_pending(account(pending_orders=[order(order_side="sell", quantity=30)]), {})

    assert state.held["005930"] == 70


def test_a_holding_with_no_pending_order_is_untouched():
    holdings = [
        {"stock_code": "005930", "total_cost": 7_800_000, "quantity": 100},
        {"stock_code": "000660", "total_cost": 4_120_000, "quantity": 10},
    ]
    state = apply_pending(account(stocks=holdings, pending_orders=[order(quantity=5)]), {})

    assert state.held == {"005930": 105, "000660": 10}


def test_a_pending_buy_for_something_not_held_yet_becomes_a_holding():
    state = apply_pending(account(pending_orders=[order(stock_code="035420", quantity=7)]), {})

    assert state.held["035420"] == 7


def test_total_cost_is_principal_and_never_reaches_the_state():
    """It is the money put in, not what the position is worth, so pricing comes from
    QuestDB's close instead."""
    cheap = apply_pending(
        account(stocks=[{"stock_code": "005930", "total_cost": 1, "quantity": 100}]), {}
    )
    dear = apply_pending(
        account(stocks=[{"stock_code": "005930", "total_cost": 999_999_999, "quantity": 100}]),
        {},
    )

    assert cheap == dear


def test_a_holding_and_a_pending_order_name_the_same_thing_alike():
    """Both spell it `stock_code`, so nothing has to be converted."""
    state = apply_pending(account(pending_orders=[order(stock_code="005930", quantity=1)]), {})

    assert state.held == {"005930": 101}


def test_an_order_that_is_not_pending_is_ignored():
    filled = order(order_status="filled", quantity=50)

    state = apply_pending(account(pending_orders=[filled]), {})

    assert state.held == {"005930": 100}
    assert state.cash == 10_000_000.0


def test_the_side_is_read_from_order_side_not_order_type():
    """`order_type` is limit or market. Reading it for the side would file a sell as
    neither a buy nor a sell and leave the holding untouched."""
    state = apply_pending(
        account(pending_orders=[order(order_side="sell", order_type="limit", quantity=30)]), {}
    )

    assert state.held["005930"] == 70


def test_a_holding_can_never_go_negative():
    state = apply_pending(account(pending_orders=[order(order_side="sell", quantity=500)]), {})

    assert state.held["005930"] == 0


def test_several_pending_orders_accumulate():
    orders = [
        order(order_id=1, limit_price=1_000, quantity=10),
        order(order_id=2, limit_price=2_000, quantity=5),
        order(order_id=3, order_side="sell", quantity=3),
    ]
    state = apply_pending(account(pending_orders=orders), {})

    assert state.cash == 10_000_000.0 - 10_000.0 - 10_000.0
    assert state.held["005930"] == 100 + 10 + 5 - 3


def test_every_account_of_a_user_is_yielded_not_just_the_first():
    """A user has several accounts and each is decided on its own."""
    accounts = [account(account_id=11), account(account_id=12), account(account_id=13)]

    yielded = list(managed_accounts([user(accounts=accounts)]))

    assert [a["account_id"] for a in yielded] == [11, 12, 13]


def test_an_inactive_account_is_skipped():
    accounts = [account(account_id=11, is_active=False), account(account_id=12)]

    yielded = list(managed_accounts([user(accounts=accounts)]))

    assert [a["account_id"] for a in yielded] == [12]


def test_accounts_of_several_users_all_come_through():
    users = [
        user(user_id=1, accounts=[account(account_id=11)]),
        user(user_id=2, accounts=[account(account_id=21), account(account_id=22)]),
    ]

    yielded = list(managed_accounts(users))

    assert [a["account_id"] for a in yielded] == [11, 21, 22]


def test_a_user_with_no_ai_accounts_is_listed_but_yields_nothing():
    """The endpoint returns every user, with an empty list for those it manages none of."""
    users = [user(user_id=1, accounts=[]), user(user_id=2, accounts=[account(account_id=21)])]

    assert [a["account_id"] for a in managed_accounts(users)] == [21]
    assert list(managed_accounts([])) == []


def test_an_account_never_carries_a_siblings_numbers():
    """Cash, holdings and pending orders are all per account."""
    first = account(
        account_id=11,
        cash_balance=1_000,
        stocks=[{"stock_code": "005930", "total_cost": 1, "quantity": 1}],
        pending_orders=[],
    )
    second = account(
        account_id=12,
        cash_balance=2_000,
        stocks=[{"stock_code": "000660", "total_cost": 2, "quantity": 2}],
        pending_orders=[order(stock_code="000660", limit_price=100, quantity=1)],
    )

    states = [apply_pending(a, {}) for a in managed_accounts([user(accounts=[first, second])])]

    assert states[0].cash == 1_000.0
    assert states[0].held == {"005930": 1}
    assert states[1].cash == 2_000.0 - 100.0
    assert states[1].held == {"000660": 3}


def test_a_reservation_is_one_order_so_nothing_is_double_counted():
    """Only the far side is an order at the Backend; the near side is a trigger this
    service watches. So a 15-share reservation commits 15 shares, not 30."""
    reservation = [order(limit_price=74_100, quantity=15)]

    state = apply_pending(account(cash_balance=2_884_000, pending_orders=reservation), {})

    assert state.cash == 2_884_000.0 - 15 * 74_100.0
    assert state.held["005930"] == 100 + 15


def test_the_backends_quote_is_never_used():
    """Every price comes from QuestDB's close, so a market order is costed at the close
    even when the Backend's `current_stock_price` rides on it."""
    pending = [order(order_type="market", limit_price=None, current_stock_price=99_000)]

    state = apply_pending(account(pending_orders=pending), {"005930": 80_000.0})

    assert state.cash == 10_000_000.0 - 10 * 80_000.0
