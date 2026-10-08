import json
from collections.abc import Callable
from typing import Any

import httpx
import jwt
from ktb_core.backend_auth import BackendAuth

SECRET = "s" * 32
ISSUER = "river-be"


def _cookies(request: httpx.Request) -> dict[str, str]:
    return dict(part.strip().split("=", 1) for part in request.headers["cookie"].split(";"))


def _claims(request: httpx.Request) -> dict[str, Any]:
    return jwt.decode(
        _cookies(request)["access_token"], SECRET, algorithms=["HS256"], issuer=ISSUER
    )


class FakeBackend:
    def __init__(self, responses: tuple[httpx.Response, ...] = ()) -> None:
        self.requests: list[httpx.Request] = []
        self.csrf_issued = 0
        self.responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
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
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(201)


def _auth(fake: Callable[[httpx.Request], httpx.Response]) -> BackendAuth:
    client = httpx.Client(base_url="https://backend", transport=httpx.MockTransport(fake))
    return BackendAuth(client, SECRET, ISSUER)


def test_get_sends_a_service_jwt_as_an_access_token_cookie():
    fake = FakeBackend()

    _auth(fake).get("/api/v1/users/ai-server", "ai-server")

    claims = _claims(fake.requests[0])
    assert claims["sub"] == "ai-server"
    assert claims["actor"] == "AI"
    assert claims["type"] == "access"
    assert int(claims["exp"]) - int(claims["iat"]) == 300


def test_mutation_sends_the_user_jwt_and_csrf_pair():
    fake = FakeBackend()

    _auth(fake).request("POST", "/api/v1/accounts/11/orders", "7", json={"quantity": 3})

    post = fake.requests[-1]
    assert _claims(post)["sub"] == "7"
    assert _cookies(post)["XSRF-TOKEN"] == "raw-1"
    assert post.headers["x-xsrf-token"] == "masked-1"
    assert json.loads(post.content) == {"quantity": 3}


def test_mutation_refreshes_csrf_and_retries_once_for_an_invalid_token():
    invalid = httpx.Response(403, json={"code": "INVALID_CSRF_TOKEN", "message": "m"})
    fake = FakeBackend((invalid,))

    _auth(fake).request("PATCH", "/api/v1/accounts/11/orders/42", "7")

    assert fake.csrf_issued == 2
    assert fake.requests[-1].headers["x-xsrf-token"] == "masked-2"


def test_csrf_refresh_preserves_an_unrelated_client_cookie():
    fake = FakeBackend()
    client = httpx.Client(base_url="https://backend", transport=httpx.MockTransport(fake))
    client.cookies.set("session", "preserve", domain="backend", path="/")

    BackendAuth(client, SECRET, ISSUER).request("POST", "/api/v1/accounts/11/orders", "7")

    assert client.cookies.get("session", domain="backend", path="/") == "preserve"
