from portfolio_rebalancer.portfolio import Explanation, Portfolio, Target
from portfolio_rebalancer.rebalance import rebalance, whole_shares
from portfolio_rebalancer.snapshot import Account

BUY = Explanation(reason="사요", reasonings=[{"label": "사요", "body": "사요"}])
SELL = Explanation(reason="팔아요", reasonings=[{"label": "팔아요", "body": "팔아요"}])
LEFT = Explanation(reason="예전에 뺐어요", reasonings=[{"label": "정리", "body": "뺐어요"}])


def hold(code, weight):
    return Target(stock_code=code, weight=weight, exiting=False, buy=BUY, sell=SELL)


def exit_(code):
    return Target(stock_code=code, weight=0.0, exiting=True, buy=None, sell=SELL)


def portfolio(*targets, leftovers=None, cash_weight=None):
    leftovers = leftovers or {}
    codes = [t.stock_code for t in targets] + list(leftovers)
    return Portfolio(
        id=1,
        cash_weight=1 - sum(t.weight for t in targets) if cash_weight is None else cash_weight,
        targets=list(targets),
        leftovers=leftovers,
        names={code: f"name-{code}" for code in codes},
    )


def account(cash, stocks=(), pending=(), active=True):
    return Account(
        account_id=11,
        is_active=active,
        cash_balance=cash,
        stocks=[{"stock_code": c, "quantity": q, "total_cost": 0} for c, q in stocks],
        pending_orders=[
            {
                "order_id": i,
                "stock_code": c,
                "order_side": side,
                "order_type": "market",
                "order_status": "pending",
                "limit_price": None,
                "quantity": q,
                "current_stock_price": price,
            }
            for i, (c, side, q, price) in enumerate(pending)
        ],
    )


def orders(result):
    return [(o.stock_code, o.side, o.quantity, o.explanation.reason) for o in result]


def test_a_share_dearer_than_its_budget_is_not_bought():
    result = rebalance(
        portfolio(hold("000660", 0.05)),
        account(10_000_000),
        {"000660": 1_800_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []


def test_a_new_holding_is_bought_to_its_whole_share_target():
    result = rebalance(
        portfolio(hold("005930", 0.5)),
        account(1_000_000),
        {"005930": 70_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 7, "사요")]


def test_drift_inside_the_band_does_not_trade():
    result = rebalance(
        portfolio(hold("005930", 0.10)),
        account(8_970_000, stocks=[("005930", 103)]),
        {"005930": 10_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []


def test_drift_outside_the_band_buys_back_to_target():
    result = rebalance(
        portfolio(hold("005930", 0.10)),
        account(9_600_000, stocks=[("005930", 40)]),
        {"005930": 10_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 60, "사요")]


def test_an_overweight_holding_is_trimmed_with_the_sell_explanation():
    result = rebalance(
        portfolio(hold("005930", 0.10)),
        account(8_000_000, stocks=[("005930", 200)]),
        {"005930": 10_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "sell", 100, "팔아요")]


def test_an_exit_sells_every_share():
    result = rebalance(
        portfolio(exit_("000660")),
        account(0, stocks=[("000660", 3)]),
        {"000660": 200_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("000660", "sell", 3, "팔아요")]


def test_a_leftover_from_an_earlier_exit_is_sold():
    result = rebalance(
        portfolio(hold("005930", 0.5), leftovers={"373220": LEFT}),
        account(0, stocks=[("373220", 2), ("005930", 10)]),
        {"005930": 70_000, "373220": 350_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert ("373220", "sell", 2, "예전에 뺐어요") in orders(result)


def test_a_holding_with_no_known_exit_is_left_alone():
    result = rebalance(
        portfolio(hold("005930", 0.5)),
        account(0, stocks=[("373220", 2), ("005930", 10)]),
        {"005930": 70_000, "373220": 350_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert all(o.stock_code != "373220" for o in result)


def test_a_pending_order_skips_its_stock_on_both_sides():
    result = rebalance(
        portfolio(hold("005930", 0.5), exit_("000660")),
        account(
            1_000_000,
            stocks=[("000660", 3)],
            pending=[("005930", "buy", 1, 70_000), ("000660", "sell", 1, 200_000)],
        ),
        {"005930": 70_000, "000660": 200_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []


def test_pending_buys_reserve_cash():
    result = rebalance(
        portfolio(hold("005930", 0.5), hold("000660", 0.5)),
        account(1_000_000, pending=[("005930", "buy", 10, 70_000)]),
        {"005930": 70_000, "000660": 100_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("000660", "buy", 1, "사요")]


def test_a_cash_shortfall_cuts_the_lowest_weight_buy():
    result = rebalance(
        portfolio(hold("005930", 0.6), hold("000660", 0.4), exit_("373220")),
        account(500_000, stocks=[("373220", 1)]),
        {"005930": 100_000, "000660": 100_000, "373220": 500_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [
        ("373220", "sell", 1, "팔아요"),
        ("005930", "buy", 5, "사요"),
    ]


def test_the_buy_buffer_leaves_room_for_a_price_rise():
    result = rebalance(
        portfolio(hold("005930", 1.0)),
        account(1_000_000),
        {"005930": 100_000},
        band=0.05,
        buy_buffer=0.02,
    )

    assert orders(result) == [("005930", "buy", 9, "사요")]


def test_a_stock_without_a_close_is_skipped_and_the_rest_trade():
    result = rebalance(
        portfolio(hold("005930", 0.5), hold("000660", 0.5)),
        account(1_000_000),
        {"005930": 100_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 5, "사요")]


def test_an_inactive_account_trades_nothing():
    result = rebalance(
        portfolio(hold("005930", 0.5)),
        account(1_000_000, active=False),
        {"005930": 70_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []


def test_a_held_stock_without_a_close_freezes_the_holdings():
    result = rebalance(
        portfolio(hold("005930", 0.5), hold("000660", 0.5)),
        account(0, stocks=[("005930", 50), ("000660", 50)]),
        {"005930": 100_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []


def test_a_held_holding_without_a_close_still_lets_exits_sell():
    result = rebalance(
        portfolio(hold("005930", 0.5), hold("000660", 0.5), exit_("373220")),
        account(0, stocks=[("005930", 50), ("000660", 50), ("373220", 2)]),
        {"005930": 100_000, "373220": 350_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("373220", "sell", 2, "팔아요")]


def test_an_exit_without_a_close_still_sells_every_share():
    result = rebalance(
        portfolio(exit_("000660")),
        account(0, stocks=[("000660", 3)]),
        {},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("000660", "sell", 3, "팔아요")]


def test_an_unpriced_stock_outside_the_portfolio_does_not_freeze_the_account():
    result = rebalance(
        portfolio(hold("005930", 0.5)),
        account(1_000_000, stocks=[("373220", 2)]),
        {"005930": 100_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 5, "사요")]


def test_a_trim_sells_to_the_unbuffered_target():
    result = rebalance(
        portfolio(hold("005930", 0.10)),
        account(8_000_000, stocks=[("005930", 200)]),
        {"005930": 10_000},
        band=0.05,
        buy_buffer=0.02,
    )

    assert orders(result) == [("005930", "sell", 100, "팔아요")]


def test_a_leftover_that_is_a_current_holding_is_never_sold_as_a_leftover():
    result = rebalance(
        portfolio(hold("005930", 0.5), leftovers={"005930": LEFT}),
        account(500_000, stocks=[("005930", 5)]),
        {"005930": 100_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert result == []


def test_a_leftover_that_is_a_current_exit_is_sold_once():
    result = rebalance(
        portfolio(exit_("000660"), leftovers={"000660": LEFT}),
        account(0, stocks=[("000660", 3)]),
        {"000660": 200_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("000660", "sell", 3, "팔아요")]


def test_every_order_carries_the_stock_name():
    result = rebalance(
        portfolio(hold("005930", 0.5), leftovers={"373220": LEFT}),
        account(1_000_000, stocks=[("373220", 2)]),
        {"005930": 100_000, "373220": 350_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert [(o.stock_code, o.stock_name) for o in result] == [
        ("373220", "name-373220"),
        ("005930", "name-005930"),
    ]


def test_cash_weight_is_held_back_before_sizing():
    result = rebalance(
        portfolio(hold("005930", 0.5), cash_weight=0.5),
        account(1_000_000),
        {"005930": 100_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 5, "사요")]


def test_an_unaffordable_weight_is_shared_equally_over_the_rest():
    result = rebalance(
        portfolio(hold("005930", 0.6), hold("035720", 0.2), hold("000660", 0.1)),
        account(1_000_000),
        {"005930": 10_000, "035720": 10_000, "000660": 1_800_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 65, "사요"), ("035720", "buy", 25, "사요")]


def test_a_redistributed_weight_sets_the_band_for_a_holding():
    result = rebalance(
        portfolio(hold("005930", 0.4), hold("000660", 0.4)),
        account(400_000, stocks=[("005930", 40)]),
        {"005930": 10_000, "000660": 1_800_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 24, "사요")]


def test_the_leftover_from_whole_shares_is_spent_one_share_at_a_time():
    result = rebalance(
        portfolio(hold("005930", 0.5), hold("000660", 0.5)),
        account(1_000_000),
        {"005930": 70_000, "000660": 300_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 10, "사요"), ("000660", "buy", 1, "사요")]


def test_a_held_stock_that_cannot_be_bought_is_kept_and_its_value_locked():
    result = rebalance(
        portfolio(hold("005930", 0.4), hold("000660", 0.1), cash_weight=0.5),
        account(1_000_000, stocks=[("000660", 1)]),
        {"005930": 10_000, "000660": 900_000},
        band=0.05,
        buy_buffer=0.0,
    )

    assert orders(result) == [("005930", "buy", 50, "사요")]


def test_whole_shares_goes_past_the_ideal_in_weight_order_once_the_short_one_is_too_dear():
    assert whole_shares(
        {"000660": 500_000, "005930": 300_000, "035720": 200_000},
        {"000660": 300_000, "005930": 100_000, "035720": 100_000},
    ) == {"000660": 1, "005930": 4, "035720": 3}
