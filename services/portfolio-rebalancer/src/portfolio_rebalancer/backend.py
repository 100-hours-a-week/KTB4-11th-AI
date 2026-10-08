from typing import Literal

import httpx
from ktb_core.backend_auth import BackendAuth
from pydantic import BaseModel

from portfolio_rebalancer.portfolio import Reasoning
from portfolio_rebalancer.rebalance import Order
from portfolio_rebalancer.snapshot import Snapshot, User


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


class Backend:
    def __init__(self, client: httpx.Client, secret: str, issuer: str) -> None:
        self._auth = BackendAuth(client, secret, issuer)

    def users(self) -> list[User]:
        response = self._auth.get("/api/v1/users/ai-server", "ai-server")
        return Snapshot.model_validate(response.json()).users

    def _send(self, method: str, path: str, user_id: int, body: dict | None = None) -> None:
        self._auth.request(method, path, str(user_id), json=body)

    def place(self, user_id: int, account_id: int, order: Order, body: dict | None = None) -> None:
        body = order_request_body(order) if body is None else body
        self._send("POST", f"/api/v1/accounts/{account_id}/orders", user_id, body)

    def cancel(self, user_id: int, account_id: int, order_id: int) -> None:
        self._send(
            "PATCH",
            f"/api/v1/accounts/{account_id}/orders/{order_id}",
            user_id,
            {"status": "cancelled"},
        )
