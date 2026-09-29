import json
import time
from dataclasses import dataclass

import httpx
import jwt as pyjwt
import pytest
from portfolio_rebalancer.request.backend import (
    bearer_token,
    build_client,
    fetch_accounts,
    send_orders,
)

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyZWJhbGFuY2VyIn0.c2lnbmF0dXJl"


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

    assert seen[0].url.path == "/api/v1/users"
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


# At least 32 bytes, or PyJWT warns that the key is shorter than the HMAC output.
SECRET = "a-shared-secret-of-at-least-thirty-two-bytes"
SUBJECT = "portfolio-rebalancer"


def test_the_token_is_signed_with_the_shared_secret():
    """It must verify with the secret the Backend holds, or every call is a 401."""
    token = bearer_token(SECRET, SUBJECT)

    claims = pyjwt.decode(token, SECRET, algorithms=["HS256"])

    assert claims["sub"] == SUBJECT


def test_a_token_signed_with_another_secret_does_not_verify():
    """Proves the signature is real rather than a bare payload."""
    token = bearer_token("another-secret-of-at-least-thirty-two-bytes", SUBJECT)

    with pytest.raises(pyjwt.InvalidSignatureError):
        pyjwt.decode(token, SECRET, algorithms=["HS256"])


def test_the_algorithm_is_declared_in_the_header():
    token = bearer_token(SECRET, SUBJECT)

    assert pyjwt.get_unverified_header(token)["alg"] == "HS256"


def test_the_token_expires_after_it_was_issued():
    claims = pyjwt.decode(bearer_token(SECRET, SUBJECT), SECRET, algorithms=["HS256"])

    assert claims["exp"] > claims["iat"]


def test_an_expired_token_is_recognised_as_expired():
    """The lifetime has to be long enough for one tick, so a fresh token is never
    already stale."""
    claims = pyjwt.decode(bearer_token(SECRET, SUBJECT), SECRET, algorithms=["HS256"])

    assert claims["exp"] - int(time.time()) > 60


def test_the_subject_comes_from_configuration():
    """The Backend decides which identity may read every user, so it is deployment
    config rather than a constant."""
    token = bearer_token(SECRET, "some-other-identity")

    assert pyjwt.decode(token, SECRET, algorithms=["HS256"])["sub"] == "some-other-identity"


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_an_empty_secret_is_refused(empty):
    """Signing with nothing would produce a token the Backend rejects, and the 401 would
    look like a claim problem rather than a missing setting."""
    with pytest.raises(ValueError, match="empty"):
        bearer_token(empty, SUBJECT)


def test_the_jwt_goes_out_on_both_calls():
    """Both /api/v1/users and /api/v1/orders are authenticated."""
    token = bearer_token(SECRET, SUBJECT)
    users, seen_users = recorder(responder({"users": []}))
    fetch_accounts(users, token)

    orders, seen_orders = recorder(responder({"message": "ok"}))
    send_orders(orders, token, [FakeOrder(11, "005930", "buy", 1)])

    assert seen_users[0].url.path == "/api/v1/users"
    assert seen_users[0].headers["authorization"] == f"Bearer {token}"
    assert seen_orders[0].url.path == "/api/v1/accounts/11/orders"
    assert seen_orders[0].headers["authorization"] == f"Bearer {token}"


def test_the_client_is_built_against_the_backend_url():
    with build_client("http://backend:8080") as client:
        assert str(client.base_url) == "http://backend:8080"


def test_orders_go_to_the_account_that_owns_them():
    """The account is part of the path, so it comes from the orders and not the caller."""
    client, seen = recorder(responder({"message": "ok"}))

    send_orders(client, TOKEN, [FakeOrder(12, "005930", "buy", 1)])

    assert seen[0].url.path == "/api/v1/accounts/12/orders"


def test_orders_spanning_two_accounts_are_refused():
    """One path can only name one account, so posting a mixed batch would put one
    account's order on another account's endpoint."""
    client, seen = recorder(responder({"message": "ok"}))
    mixed = [FakeOrder(11, "005930", "buy", 1), FakeOrder(12, "000660", "buy", 1)]

    with pytest.raises(ValueError, match="several accounts"):
        send_orders(client, TOKEN, mixed)

    assert seen == []


def test_the_account_still_travels_in_the_body_as_well():
    """The path is authoritative; the body keeps it so the Backend can cross-check."""
    client, seen = recorder(responder({"message": "ok"}))

    send_orders(client, TOKEN, [FakeOrder(11, "005930", "buy", 1)])

    assert json.loads(seen[0].content)["orders"][0]["account_id"] == 11
