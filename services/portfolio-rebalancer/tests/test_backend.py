import json

import httpx
import jwt
import pytest
from portfolio_rebalancer.backend import Backend
from portfolio_rebalancer.portfolio import Explanation
from portfolio_rebalancer.rebalance import Order, Pricing

SECRET = "s" * 32
ISSUER = "river-be"
PRICING = Pricing(
    order_type="limit",
    limit_price=74_100,
    trigger=None,
    price=78_000,
    sma=78_000,
    sigma=1_950,
    alpha=1_950,
    lower_bound=74_100,
    upper_bound=81_900,
)
ORDER = Order(
    stock_code="005930",
    stock_name="삼성전자",
    side="buy",
    quantity=3,
    explanation=Explanation(reason="사요", reasonings=[{"label": "HBM", "body": "늘었어요."}]),
    pricing=PRICING,
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
        "order_type": "limit",
        "limit_price": 74100,
        "is_upper_triggered": False,
        "is_lower_triggered": False,
        "quantity": 3,
        "reason": "사요",
        "reasoning": [{"label": "HBM", "body": "늘었어요."}],
        "holding_weight_after_trade_percent": 19.5,
        "holding_weight_limit_percent": 24.0,
    }


@pytest.mark.parametrize(
    ("quantity", "after", "limit"),
    [(100, 10.0, 12.0), (3, 0.0, 0.0)],
    ids=["trim", "sell_all"],
)
def test_a_sell_carries_its_weights(quantity, after, limit):
    fake = FakeBackend()
    sell = ORDER.model_copy(
        update={
            "side": "sell",
            "quantity": quantity,
            "holding_weight_after_trade_percent": after,
            "holding_weight_limit_percent": limit,
        }
    )

    _backend(fake).place(7, 11, sell)

    body = json.loads(fake.requests[-1].content)
    assert body["order_side"] == "sell"
    assert body["quantity"] == quantity
    assert body["holding_weight_after_trade_percent"] == after
    assert body["holding_weight_limit_percent"] == limit


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


def test_a_cancel_patches_the_order_with_no_body_and_the_csrf_pair():
    fake = FakeBackend()

    _backend(fake).cancel(7, 11, 42)

    patch = fake.requests[-1]
    assert patch.method == "PATCH"
    assert patch.url.path == "/api/v1/accounts/11/orders/42"
    assert patch.content == b""
    assert _claims(patch)["sub"] == "7"
    assert patch.headers["x-xsrf-token"] == "masked-1"


def test_a_cancel_retries_once_on_an_invalid_csrf_token():
    invalid = httpx.Response(403, json={"code": "INVALID_CSRF_TOKEN", "message": "m"})
    fake = FakeBackend([invalid])

    _backend(fake).cancel(7, 11, 42)

    assert fake.csrf_issued == 2
    assert fake.requests[-1].method == "PATCH"


def test_a_failed_cancel_raises():
    fake = FakeBackend([httpx.Response(409, json={"code": "ALREADY_FILLED", "message": "m"})])

    with pytest.raises(httpx.HTTPStatusError):
        _backend(fake).cancel(7, 11, 42)


@pytest.mark.parametrize(("side", "upper", "lower"), [("buy", True, False), ("sell", False, True)])
def test_a_market_order_flags_the_bound_its_side_escapes_through(side, upper, lower):
    fake = FakeBackend()
    market = PRICING.model_copy(
        update={"order_type": "market", "limit_price": None, "trigger": "last_run"}
    )

    _backend(fake).place(7, 11, ORDER.model_copy(update={"side": side, "pricing": market}))

    body = json.loads(fake.requests[-1].content)
    assert (body["order_type"], body["limit_price"]) == ("market", None)
    assert (body["is_upper_triggered"], body["is_lower_triggered"]) == (upper, lower)
