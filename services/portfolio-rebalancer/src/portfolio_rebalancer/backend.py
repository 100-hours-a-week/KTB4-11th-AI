import time
from http.cookies import SimpleCookie
from typing import Literal

import httpx
import jwt
from pydantic import BaseModel

from portfolio_rebalancer.portfolio import Reasoning
from portfolio_rebalancer.rebalance import Order
from portfolio_rebalancer.snapshot import Snapshot, User


class OrderRequest(BaseModel):
    stock_code: str
    stock_name: str
    order_side: Literal["buy", "sell"]
    order_type: Literal["market"] = "market"
    quantity: int
    reason: str
    thoughts: list[Reasoning]


class Backend:
    def __init__(self, client: httpx.Client, secret: str, issuer: str) -> None:
        self._client = client
        self._secret = secret
        self._issuer = issuer
        self._csrf: tuple[str, str, str] | None = None

    def _token(self, subject: str) -> str:
        issued = int(time.time())
        return jwt.encode(
            {
                "iss": self._issuer,
                "sub": subject,
                "type": "access",
                "actor": "AI",
                "iat": issued,
                "exp": issued + 300,
            },
            self._secret,
            algorithm="HS256",
        )

    def users(self) -> list[User]:
        response = self._client.get(
            "/api/v1/users/ai-server",
            headers={"Cookie": f"access_token={self._token('ai-server')}"},
        )
        response.raise_for_status()
        return Snapshot.model_validate(response.json()).users

    def _fresh_csrf(self) -> tuple[str, str, str]:
        self._client.cookies.clear()
        response = self._client.get("/api/v1/auth/csrf")
        response.raise_for_status()
        cookie = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookie.load(header)
        body = response.json()
        self._csrf = (cookie["XSRF-TOKEN"].value, body["header_name"], body["token"])
        return self._csrf

    def place(self, user_id: int, account_id: int, order: Order) -> None:
        body = OrderRequest(
            stock_code=order.stock_code,
            stock_name=order.stock_name,
            order_side=order.side,
            quantity=order.quantity,
            reason=order.explanation.reason,
            thoughts=order.explanation.reasonings,
        ).model_dump(mode="json")
        csrf = self._csrf or self._fresh_csrf()
        for attempt in range(2):
            cookie, header, token = csrf
            response = self._client.post(
                f"/api/v1/accounts/{account_id}/orders",
                json=body,
                headers={
                    "Cookie": f"access_token={self._token(str(user_id))}; XSRF-TOKEN={cookie}",
                    header: token,
                },
            )
            if attempt or response.status_code != 403 or "INVALID_CSRF_TOKEN" not in response.text:
                break
            csrf = self._fresh_csrf()
        response.raise_for_status()
