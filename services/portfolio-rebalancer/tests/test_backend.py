import pytest
from portfolio_rebalancer.backend import order_request_body
from portfolio_rebalancer.portfolio import Explanation
from portfolio_rebalancer.rebalance import Order, Pricing

PRICING = Pricing(
    order_type="limit",
    limit_price=74_100,
    trigger=None,
    price=78_000,
    sma=78_000,
    sigma=1_950,
    alpha=1_950,
    lower_bound=74_100,
    upper_bound=81_900,
)
ORDER = Order(
    stock_code="005930",
    stock_name="삼성전자",
    side="buy",
    quantity=3,
    explanation=Explanation(reason="사요", reasonings=[{"label": "HBM", "body": "늘었어요."}]),
    pricing=PRICING,
    holding_weight_after_trade_percent=19.5,
    holding_weight_limit_percent=24.0,
)


def test_a_limit_buy_carries_its_price_quantity_and_explanation():
    body = order_request_body(ORDER)

    assert (body["stock_code"], body["stock_name"]) == ("005930", "삼성전자")
    assert (body["order_side"], body["order_type"]) == ("buy", "limit")
    assert (body["limit_price"], body["quantity"]) == (74_100, 3)
    assert (body["is_upper_triggered"], body["is_lower_triggered"]) == (False, False)
    assert body["reason"] == "사요"
    assert body["reasoning"] == [{"label": "HBM", "body": "늘었어요."}]


@pytest.mark.parametrize(
    ("quantity", "after", "limit"), [(3, 19.5, 24.0), (1, 0.0, 0.0), (10, 41.2, 48.0)]
)
def test_a_sell_carries_its_weights(quantity, after, limit):
    sell = ORDER.model_copy(
        update={
            "side": "sell",
            "quantity": quantity,
            "holding_weight_after_trade_percent": after,
            "holding_weight_limit_percent": limit,
        }
    )

    body = order_request_body(sell)

    assert body["order_side"] == "sell"
    assert body["quantity"] == quantity
    assert body["holding_weight_after_trade_percent"] == after
    assert body["holding_weight_limit_percent"] == limit


@pytest.mark.parametrize(("side", "upper", "lower"), [("buy", True, False), ("sell", False, True)])
def test_a_market_order_flags_the_bound_its_side_escapes_through(side, upper, lower):
    market = PRICING.model_copy(
        update={"order_type": "market", "limit_price": None, "trigger": "last_run"}
    )

    body = order_request_body(ORDER.model_copy(update={"side": side, "pricing": market}))

    assert (body["order_type"], body["limit_price"]) == ("market", None)
    assert (body["is_upper_triggered"], body["is_lower_triggered"]) == (upper, lower)
