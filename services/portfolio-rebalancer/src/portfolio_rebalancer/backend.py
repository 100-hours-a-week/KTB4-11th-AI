import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

import httpx
import jwt

USERS_PATH = "/api/v1/users"
# Orders are placed per account, so the account is part of the path, not just the body.
ORDERS_PATH = "/api/v1/accounts/{account_id}/orders"
# Public, and the only way to read the CSRF token: the repository keeps it in a cookie
# the Backend marks httpOnly, and answers here with the token and the header to put it in.
CSRF_PATH = "/api/v1/auth/csrf"
# The Backend's BearerTokenResolver reads the access token from this cookie. It never
# looks at the Authorization header, so the token travels as a cookie or not at all.
ACCESS_COOKIE = "access_token"
# CookieCsrfTokenRepository's default, used when the Backend does not name one itself.
DEFAULT_CSRF_HEADER = "X-XSRF-TOKEN"
TIMEOUT = 10.0
ALGORITHM = "HS256"
# A tick lives for seconds, so the token needs no more life than one run plus clock skew.
TOKEN_LIFETIME = 300
# The Backend's decoder rejects a token whose `type` is anything else, and its
# OrderController answers 403 AI_ORDER_ONLY unless `actor` says AI.
TOKEN_TYPE = "access"
ACTOR = "AI"


def build_client(backend_url: str) -> httpx.Client:
    return httpx.Client(base_url=backend_url, timeout=TIMEOUT)


def access_token(secret: str, subject: str, issuer: str) -> str:
    # The claim set the Backend actually validates: `iss` has to equal its own issuer
    # (JwtValidators.createDefaultWithIssuer), `type` has to be access rather than
    # refresh, and `actor` has to be AI before OrderController will place anything.
    # `sub` is read with Long.parseLong, so it is a user id and not a service name.
    if not secret.strip():
        raise ValueError("backend_jwt_secret is empty; the Backend would answer 401")
    if not issuer.strip():
        raise ValueError("backend_jwt_issuer is empty; the Backend validates the issuer")

    issued = int(time.time())
    return jwt.encode(
        {
            "iss": issuer,
            "sub": subject,
            "type": TOKEN_TYPE,
            "actor": ACTOR,
            "iat": issued,
            "exp": issued + TOKEN_LIFETIME,
        },
        secret,
        algorithm=ALGORITHM,
    )


def authenticate(client: httpx.Client, token: str) -> None:
    # Both credentials go on the client rather than on each call: the access token as the
    # cookie the Backend reads, and the CSRF token as the header its repository matches
    # against the cookie set on this very response. Without the pair a POST is a 403
    # INVALID_CSRF_TOKEN, which reads nothing like a missing cookie.
    client.cookies.set(ACCESS_COOKIE, token)
    response = client.get(CSRF_PATH)
    response.raise_for_status()
    payload = response.json()
    client.headers[payload.get("header_name") or DEFAULT_CSRF_HEADER] = payload["token"]


def fetch_accounts(client: httpx.Client) -> list[Mapping[str, object]]:
    response = client.get(USERS_PATH, params={"state": "active"})
    response.raise_for_status()
    return response.json().get("users") or []


def send_orders(client: httpx.Client, orders: Sequence[Any]) -> None:
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
    response = client.post(ORDERS_PATH.format(account_id=account_id), json=body)
    response.raise_for_status()


def _as_payload(order: Any) -> Mapping[str, object]:
    return asdict(order) if is_dataclass(order) and not isinstance(order, type) else dict(order)
