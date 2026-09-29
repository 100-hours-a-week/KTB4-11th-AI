from portfolio_rebalancer.decide.accounts import AccountState
from portfolio_rebalancer.decide.rebalance import rebalance
from portfolio_rebalancer.decide.reservations import PRICE_BANDS
from portfolio_rebalancer.portfolio import Exit, Holding, Portfolio

SAMSUNG = ("00126380", "005930")
HYNIX = ("00164779", "000660")
NAVER = ("00266961", "035420")


def holding(pair, weight, reason="샀어야 하는 이유"):
    company_id, stock_code = pair
    return Holding(company_id=company_id, stock_code=stock_code, weight=weight, reason=reason)


def exited(pair, reason="팔아야 하는 이유"):
    company_id, stock_code = pair
    return Exit(company_id=company_id, stock_code=stock_code, reason=reason)


def portfolio(holdings=(), exits=(), cash_weight=0.0):
    return Portfolio(
        portfolio_id=42, cash_weight=cash_weight, holdings=list(holdings), exits=list(exits)
    )


def account(cash=10_000_000.0, held=None):
    return AccountState(account_id=11, cash=cash, held=dict(held or {}))


def actions(orders):
    return [(order.action, order.stock_code, order.shares) for order in orders]


def test_an_exit_is_sold_in_full():
    orders = rebalance(
        portfolio(exits=[exited(HYNIX)]),
        account(cash=0.0, held={"000660": 7}),
        {"000660": 412_000.0},
    )

    assert actions(orders) == [("sell", "000660", 7)]


def test_only_the_sell_goes_out_while_there_is_no_cash():
    """The proceeds are not money until the sell fills, so placing the buy now would put
    an order on the market with nothing behind it."""
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)], exits=[exited(HYNIX)]),
        account(cash=0.0, held={"000660": 10}),
        {"000660": 412_000.0, "005930": 78_000.0},
    )

    assert [order.action for order in orders] == ["sell"]


def test_sells_come_before_the_buys_on_the_wire():
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)], exits=[exited(HYNIX)]),
        account(cash=4_120_000.0, held={"000660": 10}),
        {"000660": 412_000.0, "005930": 78_000.0},
    )

    assert [order.action for order in orders] == ["sell", "buy"]


def test_a_part_filled_sell_funds_a_part_of_the_buy():
    """Three of ten shares sold is 1,236,000 of cash, so the buy is the shares that buys
    -- not the whole target, and not nothing."""
    plan = portfolio(holdings=[holding(SAMSUNG, 1.0)], exits=[exited(HYNIX)])
    prices = {"000660": 412_000.0, "005930": 78_000.0}

    orders = rebalance(plan, account(cash=1_236_000.0, held={"000660": 7}), prices)

    buy = next(order for order in orders if order.action == "buy")
    assert 0 < buy.shares * buy.high <= 1_236_000.0


def test_a_buy_is_never_costed_below_the_side_that_would_be_paid():
    """Only one side of a pair fills, and the high side is the one that costs money."""
    plan = portfolio(holdings=[holding(SAMSUNG, 1.0)])

    orders = rebalance(plan, account(cash=1_000_000.0), {"005930": 78_000.0})

    buy = orders[0]
    assert buy.shares * buy.high <= 1_000_000.0


def test_holding_more_than_the_target_sells_the_difference():
    """100 shares are the whole capital, and half of it has to become cash, so half the
    position is sold."""
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)], cash_weight=0.5),
        account(cash=0.0, held={"005930": 100}),
        {"005930": 78_000.0},
    )

    assert actions(orders) == [("sell", "005930", 50)]


def test_holding_exactly_the_target_emits_nothing():
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)]),
        account(cash=0.0, held={"005930": 100}),
        {"005930": 100.0},
    )

    assert [order for order in orders if order.action in {"buy", "sell"}] == []


def test_a_buy_carries_the_reason_from_the_model_portfolio():
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0, reason="반도체 업황 반등")]),
        account(cash=1_000_000.0),
        {"005930": 78_000.0},
    )

    assert orders[0].reason == "반도체 업황 반등"


def test_a_sell_carries_the_reason_from_the_exit():
    orders = rebalance(
        portfolio(exits=[exited(HYNIX, reason="비중 축소")]),
        account(cash=0.0, held={"000660": 5}),
        {"000660": 412_000.0},
    )

    assert orders[0].reason == "비중 축소"


def test_every_order_carries_the_account_it_belongs_to():
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)], exits=[exited(HYNIX)]),
        account(cash=1_000_000.0, held={"000660": 5}),
        {"005930": 78_000.0, "000660": 412_000.0},
    )

    assert {order.account_id for order in orders} == {11}


def test_an_order_goes_out_as_the_widest_pair():
    """A fresh order starts on day zero, so it gets the widest band."""
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)]),
        account(cash=1_000_000.0),
        {"005930": 78_000.0},
    )

    order = orders[0]
    assert order.band == PRICE_BANDS[0]
    assert order.reference == 78_000.0
    assert (order.low, order.high) == (78_000.0 * 0.95, 78_000.0 * 1.05)


def test_both_sides_of_the_pair_carry_the_full_quantity():
    """One quantity, quoted twice -- not split across the two sides."""
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)]),
        account(cash=8_190_000.0),
        {"005930": 78_000.0},
    )

    assert orders[0].shares == 100


def test_the_allocation_rules_apply_to_the_buy_side_only():
    """A name too dear to buy is skipped, and that must not turn into a sell."""
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 0.5), holding(NAVER, 0.5)]),
        account(cash=1_000_000.0),
        {"005930": 78_000.0, "035420": 9_000_000.0},
    )

    assert "sell" not in {order.action for order in orders}
    assert ("skip", "035420", 0) in actions(orders)


def test_a_skipped_name_that_is_held_does_not_fund_the_others():
    """Its value cannot be spent while it is not being sold, so counting it as capital
    would size the buys against money the account does not have."""
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 0.5), holding(NAVER, 0.5)]),
        account(cash=1_000_000.0, held={"035420": 1}),
        {"005930": 78_000.0, "035420": 9_000_000.0},
    )

    buy = next(order for order in orders if order.action == "buy")
    assert buy.shares * 78_000.0 <= 1_000_000.0


def test_a_skip_says_why_and_carries_no_pair():
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 0.5), holding(NAVER, 0.5)]),
        account(cash=1_000_000.0),
        {"005930": 78_000.0, "035420": 9_000_000.0},
    )

    skip = next(order for order in orders if order.action == "skip")
    assert skip.low is None and skip.high is None
    assert "budget" in skip.note


def test_cash_weight_is_held_back_from_the_buys():
    full = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)]),
        account(cash=10_000_000.0),
        {"005930": 78_000.0},
    )
    partial = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)], cash_weight=0.5),
        account(cash=10_000_000.0),
        {"005930": 78_000.0},
    )

    assert partial[0].shares < full[0].shares


def test_a_held_name_counts_towards_the_target_rather_than_being_bought_again():
    """Its market value is part of the capital the weights are applied to, so holding half
    the target means buying only the other half."""
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)]),
        account(cash=4_095_000.0, held={"005930": 50}),
        {"005930": 78_000.0},
    )

    assert actions(orders) == [("buy", "005930", 50)]


def test_a_name_with_no_price_is_skipped_rather_than_guessed():
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 0.5), holding(NAVER, 0.5)]),
        account(cash=1_000_000.0),
        {"005930": 78_000.0},
    )

    skip = next(order for order in orders if order.stock_code == "035420")
    assert skip.action == "skip"
    assert "price" in skip.note


def test_an_exit_with_no_price_is_still_sold():
    """A sell needs no price to be possible, and leaving an exit unsold is worse."""
    orders = rebalance(
        portfolio(exits=[exited(HYNIX)]),
        account(cash=0.0, held={"000660": 5}),
        {},
    )

    assert actions(orders) == [("sell", "000660", 5)]


def test_an_exit_of_something_not_held_emits_nothing():
    orders = rebalance(
        portfolio(exits=[exited(HYNIX)]),
        account(cash=0.0, held={}),
        {"000660": 412_000.0},
    )

    assert orders == []


def test_a_held_name_in_neither_the_portfolio_nor_the_exits_is_left_alone():
    """No reason exists for it, and every order has to carry one."""
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 1.0)]),
        account(cash=0.0, held={"068270": 30}),
        {"005930": 78_000.0, "068270": 200_000.0},
    )

    assert "068270" not in {order.stock_code for order in orders}


def test_an_empty_portfolio_emits_nothing():
    assert rebalance(portfolio(), account(), {}) == []


def test_a_dropped_name_that_is_held_is_never_sold():
    """Being too dear to buy is a buy-side outcome. The model portfolio still names it, so
    selling what is held would act on a decision nobody made."""
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 0.5), holding(NAVER, 0.5)]),
        account(cash=1_000_000.0, held={"035420": 1}),
        {"005930": 78_000.0, "035420": 9_000_000.0},
    )

    assert ("sell", "035420", 1) not in actions(orders)
    assert ("skip", "035420", 0) in actions(orders)


def test_a_name_with_no_price_that_is_held_is_never_sold():
    orders = rebalance(
        portfolio(holdings=[holding(SAMSUNG, 0.5), holding(NAVER, 0.5)]),
        account(cash=1_000_000.0, held={"035420": 4}),
        {"005930": 78_000.0},
    )

    assert "sell" not in {order.action for order in orders}


def test_every_price_on_an_order_lands_on_a_tick():
    """The Backend answers 400 for an off-tick price, so this has to hold for the low, the
    high and the reference that travels with them."""
    from portfolio_rebalancer.decide.reservations import tick_size

    awkward = {"005930": 1_999.0, "000660": 49_999.0, "035420": 499_999.0}
    holdings = [holding(SAMSUNG, 0.4), holding(HYNIX, 0.3), holding(NAVER, 0.3)]

    orders = rebalance(portfolio(holdings=holdings), account(cash=500_000_000.0), awkward)

    priced = [order for order in orders if order.low is not None]
    assert priced
    for order in priced:
        for price in (order.low, order.high, order.reference):
            assert price % tick_size(price) == 0, f"{order.stock_code} {price}"
