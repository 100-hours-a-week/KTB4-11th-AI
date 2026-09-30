import json
import time
from dataclasses import dataclass

import httpx
import jwt as pyjwt
import pytest
from portfolio_rebalancer.backend import (
    ACCESS_COOKIE,
    DEFAULT_CSRF_HEADER,
    access_token,
    authenticate,
    build_client,
    fetch_accounts,
    send_orders,
)

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMSJ9.c2lnbmF0dXJl"
CSRF = "a-csrf-token"


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


def backend(payload=None, status=200, header_name=DEFAULT_CSRF_HEADER):
    """Answers the CSRF handshake, and `payload` for everything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/csrf":
            return httpx.Response(
                200,
                json={"token": CSRF, "header_name": header_name},
                headers={"set-cookie": f"XSRF-TOKEN={CSRF}; Path=/"},
            )
        return httpx.Response(status, json=payload if payload is not None else {"message": "ok"})

    return handler


# ---- the CSRF handshake ----


def test_the_csrf_token_is_fetched_from_the_public_endpoint():
    """CookieCsrfTokenRepository marks the cookie httpOnly, so this endpoint is the only
    way to read the token."""
    client, seen = recorder(backend())

    authenticate(client, TOKEN)

    assert seen[0].url.path == "/api/v1/auth/csrf"
    assert seen[0].method == "GET"


def test_the_csrf_token_goes_out_on_a_post():
    """Without it the Backend answers 403 INVALID_CSRF_TOKEN."""
    client, seen = recorder(backend())
    authenticate(client, TOKEN)

    send_orders(client, [FakeOrder(11, "005930", "buy", 1)])

    assert seen[-1].headers[DEFAULT_CSRF_HEADER] == CSRF


def test_the_header_the_backend_names_is_the_one_used():
    """The handshake answers with header_name, so a rename on the Backend needs no
    change here."""
    client, seen = recorder(backend(header_name="X-CSRF-TOKEN"))
    authenticate(client, TOKEN)

    send_orders(client, [FakeOrder(11, "005930", "buy", 1)])

    assert seen[-1].headers["X-CSRF-TOKEN"] == CSRF


def test_the_csrf_cookie_is_carried_back_with_the_header():
    """The repository compares the header against the cookie it set, so one without the
    other is still a 403."""
    client, seen = recorder(backend())
    authenticate(client, TOKEN)

    send_orders(client, [FakeOrder(11, "005930", "buy", 1)])

    assert f"XSRF-TOKEN={CSRF}" in seen[-1].headers["cookie"]


def test_a_failed_handshake_raises():
    """A 500 here must not read as "no CSRF needed", which would 403 every order."""
    client, _ = recorder(lambda request: httpx.Response(500, json={"message": "boom"}))

    with pytest.raises(httpx.HTTPStatusError):
        authenticate(client, TOKEN)


# ---- the access token travels as a cookie ----


def test_the_access_token_goes_out_as_a_cookie():
    """The Backend's BearerTokenResolver reads a cookie named access_token and never
    looks at the Authorization header."""
    client, seen = recorder(backend({"users": []}))
    authenticate(client, TOKEN)

    fetch_accounts(client)

    assert f"{ACCESS_COOKIE}={TOKEN}" in seen[-1].headers["cookie"]


def test_no_authorization_header_is_sent():
    """It would be ignored, and pretending otherwise is how the 401 went unexplained."""
    client, seen = recorder(backend({"users": []}))
    authenticate(client, TOKEN)

    fetch_accounts(client)

    assert "authorization" not in seen[-1].headers


def test_the_credentials_travel_on_both_calls():
    client, seen = recorder(backend({"users": []}))
    authenticate(client, TOKEN)

    fetch_accounts(client)
    send_orders(client, [FakeOrder(11, "005930", "buy", 1)])

    for request in seen[1:]:
        assert f"{ACCESS_COOKIE}={TOKEN}" in request.headers["cookie"]
    assert seen[-1].headers[DEFAULT_CSRF_HEADER] == CSRF


# ---- polling ----


def test_the_active_users_come_back():
    users = [{"user_id": 1, "accounts": []}]
    client, _ = recorder(responder({"message": "ok", "users": users}))

    assert fetch_accounts(client) == users


def test_only_active_users_are_asked_for():
    client, seen = recorder(responder({"users": []}))

    fetch_accounts(client)

    assert seen[0].url.path == "/api/v1/users"
    assert dict(seen[0].url.params) == {"state": "active"}


def test_a_payload_without_users_reads_as_none_rather_than_raising():
    client, _ = recorder(responder({"message": "ok"}))

    assert fetch_accounts(client) == []


def test_a_failed_poll_raises():
    """A 500 must not read as zero active users, which would silently skip everyone."""
    client, _ = recorder(responder({"message": "boom"}, status=500))

    with pytest.raises(httpx.HTTPStatusError):
        fetch_accounts(client)


# ---- orders ----


def test_orders_go_out_as_sent():
    orders = [
        FakeOrder(account_id=11, stock_code="005930", action="buy", shares=10),
        FakeOrder(account_id=11, stock_code="000660", action="sell", shares=2),
    ]
    client, seen = recorder(responder({"message": "ok"}))

    send_orders(client, orders)

    body = json.loads(seen[0].content)
    assert body["orders"] == [
        {"account_id": 11, "stock_code": "005930", "action": "buy", "shares": 10},
        {"account_id": 11, "stock_code": "000660", "action": "sell", "shares": 2},
    ]


def test_sending_nothing_makes_no_request():
    client, seen = recorder(responder({"message": "ok"}))

    send_orders(client, [])

    assert seen == []


def test_a_rejected_send_raises():
    client, _ = recorder(responder({"message": "nope"}, status=400))

    with pytest.raises(httpx.HTTPStatusError):
        send_orders(client, [FakeOrder(11, "005930", "buy", 1)])


def test_orders_go_to_the_account_that_owns_them():
    """The account is part of the path, so it comes from the orders and not the caller."""
    client, seen = recorder(responder({"message": "ok"}))

    send_orders(client, [FakeOrder(12, "005930", "buy", 1)])

    assert seen[0].url.path == "/api/v1/accounts/12/orders"


def test_orders_spanning_two_accounts_are_refused():
    """One path can only name one account, so posting a mixed batch would put one
    account's order on another account's endpoint."""
    client, seen = recorder(responder({"message": "ok"}))
    mixed = [FakeOrder(11, "005930", "buy", 1), FakeOrder(12, "000660", "buy", 1)]

    with pytest.raises(ValueError, match="several accounts"):
        send_orders(client, mixed)

    assert seen == []


def test_the_account_still_travels_in_the_body_as_well():
    """The path is authoritative; the body keeps it so the Backend can cross-check."""
    client, seen = recorder(responder({"message": "ok"}))

    send_orders(client, [FakeOrder(11, "005930", "buy", 1)])

    assert json.loads(seen[0].content)["orders"][0]["account_id"] == 11


def test_the_client_is_built_against_the_backend_url():
    with build_client("http://backend:8080") as client:
        assert str(client.base_url) == "http://backend:8080"


# ---- the token's claims ----

# At least 32 bytes, or the Backend refuses the key outright.
SECRET = "a-shared-secret-of-at-least-thirty-two-bytes"
SUBJECT = "11"
ISSUER = "https://stock-spoon.com"


def claims(token=None):
    return pyjwt.decode(
        token or access_token(SECRET, SUBJECT, ISSUER),
        SECRET,
        algorithms=["HS256"],
        issuer=ISSUER,
    )


def test_the_token_is_signed_with_the_shared_secret():
    """It must verify with the secret the Backend holds, or every call is a 401."""
    assert claims()["sub"] == SUBJECT


def test_a_token_signed_with_another_secret_does_not_verify():
    """Proves the signature is real rather than a bare payload."""
    token = access_token("another-secret-of-at-least-thirty-two-bytes", SUBJECT, ISSUER)

    with pytest.raises(pyjwt.InvalidSignatureError):
        pyjwt.decode(token, SECRET, algorithms=["HS256"], issuer=ISSUER)


def test_the_issuer_is_claimed():
    """The Backend's decoder is built with createDefaultWithIssuer, so a token with no
    issuer, or the wrong one, is rejected before any route is reached."""
    assert claims()["iss"] == ISSUER


def test_a_token_from_another_issuer_is_recognised_as_wrong():
    token = access_token(SECRET, SUBJECT, "https://somewhere-else")

    with pytest.raises(pyjwt.InvalidIssuerError):
        pyjwt.decode(token, SECRET, algorithms=["HS256"], issuer=ISSUER)


def test_the_token_type_is_access_not_refresh():
    """The Backend runs a validator that compares the type claim, and the refresh
    decoder is a different bean."""
    assert claims()["type"] == "access"


def test_the_actor_says_ai():
    """OrderController answers 403 AI_ORDER_ONLY for anything else."""
    assert claims()["actor"] == "AI"


def test_the_algorithm_is_declared_in_the_header():
    token = access_token(SECRET, SUBJECT, ISSUER)

    assert pyjwt.get_unverified_header(token)["alg"] == "HS256"


def test_the_token_expires_after_it_was_issued():
    issued = claims()

    assert issued["exp"] > issued["iat"]


def test_an_expired_token_is_recognised_as_expired():
    """The lifetime has to be long enough for one tick, so a fresh token is never
    already stale."""
    assert claims()["exp"] - int(time.time()) > 60


def test_the_subject_comes_from_configuration():
    """The Backend parses it as a long, so it is the user id whose accounts are being
    rebalanced rather than a fixed service name."""
    assert claims(access_token(SECRET, "4242", ISSUER))["sub"] == "4242"


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_an_empty_secret_is_refused(empty):
    """Signing with nothing would produce a token the Backend rejects, and the 401 would
    look like a claim problem rather than a missing setting."""
    with pytest.raises(ValueError, match="empty"):
        access_token(empty, SUBJECT, ISSUER)


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_an_empty_issuer_is_refused(empty):
    with pytest.raises(ValueError, match="empty"):
        access_token(SECRET, SUBJECT, empty)
