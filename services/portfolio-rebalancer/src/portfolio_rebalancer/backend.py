import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

import httpx
import jwt

USERS_PATH = "/api/v1/users"
# Orders are placed per account, so the account is part of the path, not just the body.
ORDERS_PATH = "/api/v1/accounts/{account_id}/orders"
TIMEOUT = 10.0
ALGORITHM = "HS256"
# A tick lives for seconds, so the token needs no more life than one run plus clock skew.
TOKEN_LIFETIME = 300


def build_client(backend_url: str) -> httpx.Client:
    return httpx.Client(base_url=backend_url, timeout=TIMEOUT)


def bearer_token(secret: str, subject: str) -> str:
    # The claim set is `sub`, `iat` and `exp`, and it is unverified. GET /api/v1/users
    # does not exist on the Backend yet, and its filter answers 401 for every path -- even
    # ones that do not exist -- so no response can tell a rejected token from a missing
    # route. If the Backend turns out to want more (a role, an audience, an issuer) they are
    # added here and nowhere else.
    if not secret.strip():
        raise ValueError("backend_jwt_secret is empty; the Backend would answer 401")

    issued = int(time.time())
    return jwt.encode(
        {"sub": subject, "iat": issued, "exp": issued + TOKEN_LIFETIME},
        secret,
        algorithm=ALGORITHM,
    )


def fetch_accounts(client: httpx.Client, token: str) -> list[Mapping[str, object]]:
    response = client.get(USERS_PATH, params={"state": "active"}, headers=_auth(token))
    response.raise_for_status()
    return response.json().get("users") or []


def send_orders(client: httpx.Client, token: str, orders: Sequence[Any]) -> None:
    # The account comes from the orders rather than the caller, so one account's orders can
    # never be posted to another account's endpoint. Mixing accounts in one call is refused
    # for the same reason: the path can only name one.
    if not orders:
        return

    accounts = {order.account_id for order in orders}
    if len(accounts) > 1:
        raise ValueError(f"orders span several accounts, which one path cannot name: {accounts}")

    account_id = accounts.pop()
    body = {"orders": [_as_payload(order) for order in orders]}
    response = client.post(
        ORDERS_PATH.format(account_id=account_id), json=body, headers=_auth(token)
    )
    response.raise_for_status()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _as_payload(order: Any) -> Mapping[str, object]:
    return asdict(order) if is_dataclass(order) and not isinstance(order, type) else dict(order)
