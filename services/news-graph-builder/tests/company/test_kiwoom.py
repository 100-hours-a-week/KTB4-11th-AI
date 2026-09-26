import json

import httpx
import pytest
from news_graph_builder.company import fetch_kospi
from news_graph_builder.company.settings import CompanySettings

BASE_URI = "https://kiwoom.test"


def kiwoom(pages: list[tuple[list[dict], dict]], token_reply: dict | None = None, seen=None):
    remaining = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=token_reply or {"return_code": 0, "token": "tok"})
        rows, headers = remaining.pop(0)
        return httpx.Response(
            200, json={"return_code": 0, "return_msg": "ok", "list": rows}, headers=headers
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def fetch(client):
    settings = CompanySettings(
        kiwoom_app_key="app",
        kiwoom_secret_key="secret",
        kiwoom_base_uri=BASE_URI,
        dart_api_key="dart",
    )
    return fetch_kospi(client, settings=settings)


def test_follows_continuation_pages():
    seen = []
    client = kiwoom(
        [
            ([{"code": "005930", "name": "삼성전자"}], {"cont-yn": "Y", "next-key": "k1"}),
            ([{"code": "000660", "name": "SK하이닉스"}], {"cont-yn": "N", "next-key": ""}),
        ],
        seen=seen,
    )

    assert fetch(client) == [("005930", "삼성전자"), ("000660", "SK하이닉스")]

    token, first, second = seen
    assert json.loads(token.content) == {
        "grant_type": "client_credentials",
        "appkey": "app",
        "secretkey": "secret",
    }
    assert first.url.path == "/api/dostk/stkinfo"
    assert first.headers["api-id"] == "ka10099"
    assert first.headers["authorization"] == "Bearer tok"
    assert first.headers["cont-yn"] == "N"
    assert json.loads(first.content) == {"mrkt_tp": "0"}
    assert (second.headers["cont-yn"], second.headers["next-key"]) == ("Y", "k1")


def test_a_refused_token_raises():
    client = kiwoom([], token_reply={"return_code": 3, "return_msg": "invalid appkey"})

    with pytest.raises(RuntimeError, match="invalid appkey"):
        fetch(client)


def test_a_failed_page_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"return_code": 0, "token": "tok"})
        return httpx.Response(200, json={"return_code": 5, "return_msg": "rate limited"})

    with pytest.raises(RuntimeError, match="rate limited"):
        fetch(httpx.Client(transport=httpx.MockTransport(handler)))
