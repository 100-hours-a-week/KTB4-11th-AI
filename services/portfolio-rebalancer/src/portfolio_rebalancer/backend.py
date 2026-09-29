"""The Backend's HTTP client. Every request to the Backend goes through here.

The token lives in this module and nowhere else, so settling how it is issued changes one
file. `market-collector`'s Kiwoom client is the precedent.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

import httpx

__all__ = ["acquire_token", "build_client", "fetch_accounts", "send_orders"]

USERS_PATH = "/users"
ORDERS_PATH = "/orders"
TIMEOUT = 10.0


def build_client(backend_url: str) -> httpx.Client:
    return httpx.Client(base_url=backend_url, timeout=TIMEOUT)


def acquire_token(client: httpx.Client) -> str:
    """Obtain a token for the Backend.

    How the token is issued is not settled yet. Until it is, this refuses rather than
    returning an empty string, which would send unauthenticated orders.
    """
    raise NotImplementedError("the Backend token scheme is not settled yet")


def fetch_accounts(client: httpx.Client, token: str) -> list[Mapping[str, object]]:
    """GET /users?state=active."""
    response = client.get(USERS_PATH, params={"state": "active"}, headers=_auth(token))
    response.raise_for_status()
    return response.json().get("users") or []


def send_orders(client: httpx.Client, token: str, orders: Sequence[Any]) -> None:
    """Place each order as its pair of reservations."""
    if not orders:
        return
    body = {"orders": [_as_payload(order) for order in orders]}
    response = client.post(ORDERS_PATH, json=body, headers=_auth(token))
    response.raise_for_status()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _as_payload(order: Any) -> Mapping[str, object]:
    """Serialise without importing the order's own module, which keeps this client
    ignorant of how a rebalance decides things."""
    return asdict(order) if is_dataclass(order) and not isinstance(order, type) else dict(order)
