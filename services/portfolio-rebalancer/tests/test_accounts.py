import pytest
from portfolio_rebalancer.decide.accounts import apply_pending, managed_accounts
from portfolio_rebalancer.decide.reservations import reservation_prices


def account(**changes):
    values = {
        "account_id": 11,
        "account_name": "기본 계좌2",
        "is_ai_managed": True,
        "is_duel_account": False,
        "is_active": True,
        "cash_balance": 10_000_000.0,
        "stocks": [{"stock_id": "005930", "total_price": 7_800_000, "amount": 100}],
        "pending_orders": [],
    }
    return values | changes


def user(**changes):
    values = {
        "user_id": 1,
        "nickname": "스톡스푼",
        "state": "active",
        "accounts": [account()],
    }
    return values | changes


def order(**changes):
    values = {
        "order_type": "buy",
        "status": "pending",
        "stock_code": "005930",
        "price": 78_000.0,
        "amount": 10,
    }
    return values | changes


def test_cash_and_holdings_come_through_when_nothing_is_pending():
    state = apply_pending(account())

    assert state.account_id == 11
    assert state.cash == 10_000_000.0
    assert state.held == {"005930": 100}


def test_a_pending_buy_is_not_cash_this_service_may_spend():
    state = apply_pending(account(pending_orders=[order(price=78_000.0, amount=10)]))

    assert state.cash == 10_000_000.0 - 780_000.0


def test_a_pending_sell_does_not_add_cash_before_it_fills():
    """Proceeds are not spendable until the order actually fills."""
    state = apply_pending(account(pending_orders=[order(order_type="sell", amount=10)]))

    assert state.cash == 10_000_000.0


def test_a_pending_buy_raises_the_quantity_the_account_is_on_its_way_to_holding():
    state = apply_pending(account(pending_orders=[order(amount=10)]))

    assert state.held["005930"] == 110


def test_a_pending_sell_lowers_it():
    state = apply_pending(account(pending_orders=[order(order_type="sell", amount=30)]))

    assert state.held["005930"] == 70


def test_a_holding_with_no_pending_order_is_untouched():
    holdings = [
        {"stock_id": "005930", "total_price": 7_800_000, "amount": 100},
        {"stock_id": "000660", "total_price": 4_120_000, "amount": 10},
    ]
    state = apply_pending(account(stocks=holdings, pending_orders=[order(amount=5)]))

    assert state.held == {"005930": 105, "000660": 10}


def test_a_pending_buy_for_something_not_held_yet_becomes_a_holding():
    state = apply_pending(account(pending_orders=[order(stock_code="035420", amount=7)]))

    assert state.held["035420"] == 7


def test_total_price_is_principal_and_never_reaches_the_state():
    """It is the money put in, not what the position is worth, so pricing must come from
    QuestDB instead."""
    cheap = apply_pending(account(stocks=[{"stock_id": "005930", "total_price": 1, "amount": 100}]))
    dear = apply_pending(
        account(stocks=[{"stock_id": "005930", "total_price": 999_999_999, "amount": 100}])
    )

    assert cheap == dear


def test_stock_id_becomes_stock_code():
    """A holding and a pending order have to name the same thing the same way."""
    state = apply_pending(account(pending_orders=[order(stock_code="005930", amount=1)]))

    assert state.held == {"005930": 101}


def test_an_order_that_is_not_pending_is_ignored():
    filled = order(status="filled", amount=50)

    state = apply_pending(account(pending_orders=[filled]))

    assert state.held == {"005930": 100}
    assert state.cash == 10_000_000.0


def test_a_fractional_pending_amount_is_floored():
    """The payload types amount as a float, but KRX trades whole shares, so the quantity
    is rounded down rather than overstating what the account will hold."""
    state = apply_pending(account(pending_orders=[order(amount=10.9)]))

    assert state.held["005930"] == 110


def test_a_holding_can_never_go_negative():
    state = apply_pending(account(pending_orders=[order(order_type="sell", amount=500)]))

    assert state.held["005930"] == 0


def test_several_pending_orders_accumulate():
    orders = [
        order(price=1_000.0, amount=10),
        order(price=2_000.0, amount=5),
        order(order_type="sell", amount=3),
    ]
    state = apply_pending(account(pending_orders=orders))

    assert state.cash == 10_000_000.0 - 10_000.0 - 10_000.0
    assert state.held["005930"] == 100 + 10 + 5 - 3


def test_every_account_of_a_user_is_yielded_not_just_the_first():
    """A user has several accounts and each is decided on its own."""
    accounts = [account(account_id=11), account(account_id=12), account(account_id=13)]

    yielded = list(managed_accounts([user(accounts=accounts)]))

    assert [a["account_id"] for a in yielded] == [11, 12, 13]


def test_an_unmanaged_account_is_skipped():
    accounts = [account(account_id=11, is_ai_managed=False), account(account_id=12)]

    yielded = list(managed_accounts([user(accounts=accounts)]))

    assert [a["account_id"] for a in yielded] == [12]


def test_an_inactive_account_is_skipped():
    accounts = [account(account_id=11, is_active=False), account(account_id=12)]

    yielded = list(managed_accounts([user(accounts=accounts)]))

    assert [a["account_id"] for a in yielded] == [12]


@pytest.mark.parametrize(
    ("managed", "active"),
    [(False, False), (False, True), (True, False)],
)
def test_both_flags_are_needed(managed, active):
    accounts = [account(is_ai_managed=managed, is_active=active)]

    assert list(managed_accounts([user(accounts=accounts)])) == []


def test_accounts_of_several_users_all_come_through():
    users = [
        user(user_id=1, accounts=[account(account_id=11)]),
        user(user_id=2, accounts=[account(account_id=21), account(account_id=22)]),
    ]

    yielded = list(managed_accounts(users))

    assert [a["account_id"] for a in yielded] == [11, 21, 22]


def test_an_account_never_carries_a_siblings_numbers():
    """Cash, holdings and pending orders are all per account."""
    first = account(
        account_id=11,
        cash_balance=1_000.0,
        stocks=[{"stock_id": "005930", "total_price": 1, "amount": 1}],
        pending_orders=[],
    )
    second = account(
        account_id=12,
        cash_balance=2_000.0,
        stocks=[{"stock_id": "000660", "total_price": 2, "amount": 2}],
        pending_orders=[order(stock_code="000660", price=100.0, amount=1)],
    )

    states = [apply_pending(a) for a in managed_accounts([user(accounts=[first, second])])]

    assert states[0].cash == 1_000.0
    assert states[0].held == {"005930": 1}
    assert states[1].cash == 2_000.0 - 100.0
    assert states[1].held == {"000660": 3}


def test_a_single_account_object_is_accepted_as_well_as_a_list():
    """The example payload spells accounts as an object; the Backend sends a list. Either
    shape has to work, because which one arrives is not yet confirmed."""
    yielded = list(managed_accounts([user(accounts=account(account_id=11))]))

    assert [a["account_id"] for a in yielded] == [11]


def test_a_user_with_no_accounts_yields_nothing():
    assert list(managed_accounts([user(accounts=[])])) == []
    assert list(managed_accounts([])) == []


def reserved(stock_code="005930", order_type="buy", low=74_100.0, high=81_900.0, amount=15):
    """A reservation as the poll reports it: two orders, both for the full quantity."""
    return [
        order(order_type=order_type, stock_code=stock_code, price=low, amount=amount),
        order(order_type=order_type, stock_code=stock_code, price=high, amount=amount),
    ]


def test_a_reservation_commits_its_cash_once_not_twice():
    """Only one side of a pair can fill. Counting both commits twice the cash, which shows
    up as buying far less than the account can afford."""
    state = apply_pending(account(cash_balance=2_884_000.0, pending_orders=reserved()))

    assert state.cash == 2_884_000.0 - 15 * 81_900.0


def test_a_reservation_commits_at_the_high_side():
    """That is the side that would actually be paid."""
    state = apply_pending(account(cash_balance=10_000_000.0, pending_orders=reserved()))

    assert state.cash == 10_000_000.0 - 15 * 81_900.0


def test_a_reservation_moves_the_quantity_once_not_twice():
    state = apply_pending(account(stocks=[], pending_orders=reserved(amount=15)))

    assert state.held == {"005930": 15}


def test_a_sell_reservation_also_counts_once():
    held = [{"stock_id": "000660", "total_price": 1, "amount": 10}]
    low, high = reservation_prices(412_000.0, 0)
    pending = reserved(stock_code="000660", order_type="sell", low=low, high=high, amount=4)

    state = apply_pending(account(stocks=held, pending_orders=pending))

    assert state.held == {"000660": 6}


def test_a_lone_order_is_still_counted_on_its_own():
    """Half a pair means the other side filled, so what is left is a single commitment."""
    lone = [order(price=81_900.0, amount=15)]

    state = apply_pending(account(cash_balance=2_884_000.0, pending_orders=lone))

    assert state.cash == 2_884_000.0 - 15 * 81_900.0
    assert state.held["005930"] == 100 + 15
