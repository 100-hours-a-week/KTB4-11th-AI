import json
from dataclasses import dataclass

import httpx
import pytest
from portfolio_rebalancer_http.backend import (
    acquire_token,
    build_client,
    fetch_accounts,
    send_orders,
)

TOKEN = "a-token"


@dataclass(frozen=True)
class FakeOrder:
    account_id: int
    stock_code: str
    action: str
    shares: int


def recorder(handler):
    """A client over MockTransport, plus the list of requests it saw."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record), base_url="http://backend"), seen


def responder(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


def test_the_active_users_come_back():
    users = [{"user_id": 1, "accounts": []}]
    client, _ = recorder(responder({"message": "ok", "users": users}))

    assert fetch_accounts(client, TOKEN) == users


def test_only_active_users_are_asked_for():
    client, seen = recorder(responder({"users": []}))

    fetch_accounts(client, TOKEN)

    assert seen[0].url.path == "/users"
    assert dict(seen[0].url.params) == {"state": "active"}


def test_the_token_goes_out_as_a_bearer_header():
    client, seen = recorder(responder({"users": []}))

    fetch_accounts(client, TOKEN)

    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_a_payload_without_users_reads_as_none_rather_than_raising():
    client, _ = recorder(responder({"message": "ok"}))

    assert fetch_accounts(client, TOKEN) == []


def test_a_failed_poll_raises():
    """A 500 must not read as zero active users, which would silently skip everyone."""
    client, _ = recorder(responder({"message": "boom"}, status=500))

    with pytest.raises(httpx.HTTPStatusError):
        fetch_accounts(client, TOKEN)


def test_orders_go_out_as_sent():
    orders = [
        FakeOrder(account_id=11, stock_code="005930", action="buy", shares=10),
        FakeOrder(account_id=11, stock_code="000660", action="sell", shares=2),
    ]
    client, seen = recorder(responder({"message": "ok"}))

    send_orders(client, TOKEN, orders)

    body = json.loads(seen[0].content)
    assert body["orders"] == [
        {"account_id": 11, "stock_code": "005930", "action": "buy", "shares": 10},
        {"account_id": 11, "stock_code": "000660", "action": "sell", "shares": 2},
    ]


def test_sending_orders_carries_the_token_too():
    client, seen = recorder(responder({"message": "ok"}))

    send_orders(client, TOKEN, [FakeOrder(11, "005930", "buy", 1)])

    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert seen[0].method == "POST"


def test_sending_nothing_makes_no_request():
    client, seen = recorder(responder({"message": "ok"}))

    send_orders(client, TOKEN, [])

    assert seen == []


def test_a_rejected_send_raises():
    client, _ = recorder(responder({"message": "nope"}, status=400))

    with pytest.raises(httpx.HTTPStatusError):
        send_orders(client, TOKEN, [FakeOrder(11, "005930", "buy", 1)])


def test_acquiring_a_token_fails_loudly_while_the_scheme_is_unsettled():
    """How the token is issued has not been decided. Until it is, this must refuse rather
    than hand back an empty string, which would send an unauthenticated order."""
    client, _ = recorder(responder({}))

    with pytest.raises(NotImplementedError, match="token"):
        acquire_token(client)


def test_the_client_is_built_against_the_backend_url():
    with build_client("http://backend:8080") as client:
        assert str(client.base_url) == "http://backend:8080"
