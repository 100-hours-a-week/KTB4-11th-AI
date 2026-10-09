from typing import Literal

from pydantic import BaseModel

from portfolio_rebalancer.portfolio import Reasoning
from portfolio_rebalancer.rebalance import Order


class OrderRequest(BaseModel):
    stock_code: str
    stock_name: str
    order_side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"]
    limit_price: int | None
    is_upper_triggered: bool
    is_lower_triggered: bool
    quantity: int
    reason: str
    reasoning: list[Reasoning]
    holding_weight_after_trade_percent: float
    holding_weight_limit_percent: float


def order_request_body(order: Order) -> dict:
    market = order.pricing.order_type == "market"
    return OrderRequest(
        stock_code=order.stock_code,
        stock_name=order.stock_name,
        order_side=order.side,
        order_type=order.pricing.order_type,
        limit_price=order.pricing.limit_price,
        is_upper_triggered=market and order.side == "buy",
        is_lower_triggered=market and order.side == "sell",
        quantity=order.quantity,
        reason=order.explanation.reason,
        reasoning=order.explanation.reasonings,
        holding_weight_after_trade_percent=order.holding_weight_after_trade_percent,
        holding_weight_limit_percent=order.holding_weight_limit_percent,
    ).model_dump(mode="json")
