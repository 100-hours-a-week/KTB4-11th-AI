import json

import httpx
import jwt
import pytest
from portfolio_rebalancer.backend import Backend
from portfolio_rebalancer.portfolio import Explanation
from portfolio_rebalancer.rebalance import Order

SECRET = "s" * 32
ISSUER = "river-be"
ORDER = Order(
    stock_code="005930",
    stock_name="삼성전자",
    side="buy",
    quantity=3,
    explanation=Explanation(reason="사요", reasonings=[{"label": "HBM", "body": "늘었어요."}]),
    holding_weight_after_trade_percent=19.5,
    holding_weight_limit_percent=24.0,
)


def _cookies(request):
    return dict(part.strip().split("=", 1) for part in request.headers["cookie"].split(";"))


def _claims(request):
    return jwt.decode(
        _cookies(request)["access_token"], SECRET, algorithms=["HS256"], issuer=ISSUER
    )


class FakeBackend:
    def __init__(self, order_responses=()):
        self.requests = []
        self.csrf_issued = 0
        self.order_responses = list(order_responses)

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path == "/api/v1/auth/csrf":
            self.csrf_issued += 1
            n = self.csrf_issued
            return httpx.Response(
                200,
                json={"token": f"masked-{n}", "header_name": "X-XSRF-TOKEN"},
                headers={
                    "set-cookie": f"XSRF-TOKEN=raw-{n}; Path=/; Secure; HttpOnly; SameSite=Lax"
                },
            )
        if request.url.path == "/api/v1/users/ai-server":
            return httpx.Response(200, json={"users": [{"user_id": 1, "accounts": []}]})
        if self.order_responses:
            return self.order_responses.pop(0)
        return httpx.Response(201, json={"order_id": 9})


def _backend(fake):
    client = httpx.Client(base_url="http://backend", transport=httpx.MockTransport(fake))
    return Backend(client, SECRET, ISSUER)


def test_users_sends_the_service_token_as_a_cookie():
    fake = FakeBackend()

    users = _backend(fake).users()

    assert users[0].accounts == []
    claims = _claims(fake.requests[0])
    assert claims["sub"] == "ai-server"
    assert claims["actor"] == "AI"
    assert claims["type"] == "access"
    assert claims["exp"] - claims["iat"] == 300
    assert "authorization" not in fake.requests[0].headers


def test_an_order_carries_the_user_token_the_csrf_pair_and_the_explanation():
    fake = FakeBackend()

    _backend(fake).place(7, 11, ORDER)

    post = fake.requests[-1]
    assert post.method == "POST"
    assert post.url.path == "/api/v1/accounts/11/orders"
    assert _claims(post)["sub"] == "7"
    assert _cookies(post)["XSRF-TOKEN"] == "raw-1"
    assert post.headers["x-xsrf-token"] == "masked-1"
    assert json.loads(post.content) == {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "order_side": "buy",
        "order_type": "market",
        "quantity": 3,
        "reason": "사요",
        "thoughts": [{"label": "HBM", "body": "늘었어요."}],
        "holding_weight_after_trade_percent": 19.5,
        "holding_weight_limit_percent": 24.0,
    }


def test_the_csrf_token_is_fetched_once_per_run():
    fake = FakeBackend()
    backend = _backend(fake)

    backend.place(7, 11, ORDER)
    backend.place(8, 12, ORDER)

    assert fake.csrf_issued == 1


def test_an_invalid_csrf_token_is_refreshed_and_the_order_retried_once():
    invalid = httpx.Response(403, json={"code": "INVALID_CSRF_TOKEN", "message": "m"})
    fake = FakeBackend([invalid])

    _backend(fake).place(7, 11, ORDER)

    assert fake.csrf_issued == 2
    assert fake.requests[-1].headers["x-xsrf-token"] == "masked-2"


def test_any_other_403_is_raised_without_a_retry():
    forbidden = httpx.Response(403, json={"code": "AI_ORDER_ONLY", "message": "m"})
    fake = FakeBackend([forbidden])

    with pytest.raises(httpx.HTTPStatusError):
        _backend(fake).place(7, 11, ORDER)

    assert fake.csrf_issued == 1
    assert sum(r.method == "POST" for r in fake.requests) == 1


class SecureCookieFakeBackend:
    def __init__(self):
        self.requests = []
        self.csrf_issued = 0
        self.post_count = 0

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path == "/api/v1/auth/csrf":
            self.csrf_issued += 1
            n = self.csrf_issued
            has_cookie = "XSRF-TOKEN" in request.headers.get("cookie", "")
            if has_cookie:
                return httpx.Response(
                    200, json={"token": f"masked-{n}", "header_name": "X-XSRF-TOKEN"}
                )
            return httpx.Response(
                200,
                json={"token": f"masked-{n}", "header_name": "X-XSRF-TOKEN"},
                headers={
                    "set-cookie": f"XSRF-TOKEN=raw-{n}; Path=/; Secure; HttpOnly; SameSite=Lax"
                },
            )
        if request.method == "POST":
            self.post_count += 1
            if self.post_count == 1:
                return httpx.Response(403, json={"code": "INVALID_CSRF_TOKEN", "message": "m"})
        return httpx.Response(201, json={"order_id": 9})


def test_csrf_retry_clears_cookies_for_https_with_secure_flag():
    fake = SecureCookieFakeBackend()
    client = httpx.Client(base_url="https://backend", transport=httpx.MockTransport(fake))
    backend = Backend(client, SECRET, ISSUER)

    backend.place(7, 11, ORDER)

    assert fake.csrf_issued == 2
    assert fake.requests[-1].headers["x-xsrf-token"] == "masked-2"
