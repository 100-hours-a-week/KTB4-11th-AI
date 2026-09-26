import json

import httpx
import pytest
from news_graph_builder.kiwoom import KiwoomSettings, fetch_pages, fetch_token
from news_graph_builder.kiwoom import client as kiwoom_client

BASE_URI = "https://kiwoom.test"
SETTINGS = KiwoomSettings(
    kiwoom_app_key="app",
    kiwoom_secret_key="secret",
    kiwoom_base_uri=BASE_URI,
    kiwoom_request_interval=0,
)


def replying(pages: list[tuple[dict, dict]], seen: list) -> httpx.Client:
    remaining = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body, headers = remaining.pop(0)
        return httpx.Response(200, json=body, headers=headers)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_token_posts_the_app_credentials():
    seen = []
    client = replying([({"return_code": 0, "token": "tok"}, {})], seen)

    token = fetch_token(client, settings=SETTINGS)

    assert token == "tok"
    assert str(seen[0].url) == f"{BASE_URI}/oauth2/token"
    assert json.loads(seen[0].content) == {
        "grant_type": "client_credentials",
        "appkey": "app",
        "secretkey": "secret",
    }


def test_a_refused_token_raises():
    client = replying([({"return_code": 3, "return_msg": "invalid appkey"}, {})], [])

    with pytest.raises(RuntimeError, match="invalid appkey"):
        fetch_token(client, settings=SETTINGS)


def test_fetch_pages_follows_continuation_and_sleeps_between_requests(monkeypatch):
    sleeps = []
    monkeypatch.setattr(kiwoom_client.time, "sleep", sleeps.append)
    seen = []
    client = replying(
        [
            ({"return_code": 0, "rows": [{"a": 1}]}, {"cont-yn": "Y", "next-key": "k1"}),
            ({"return_code": 0, "rows": [{"a": 2}]}, {"cont-yn": "N", "next-key": ""}),
        ],
        seen,
    )
    settings = SETTINGS.model_copy(update={"kiwoom_request_interval": 0.5})

    rows = fetch_pages(
        client,
        token="tok",
        api_id="ka99999",
        path="thme",
        body={"x": "1"},
        list_key="rows",
        settings=settings,
    )

    assert rows == [{"a": 1}, {"a": 2}]
    assert sleeps == [0.5, 0.5]
    first, second = seen
    assert str(first.url) == f"{BASE_URI}/api/dostk/thme"
    assert first.headers["api-id"] == "ka99999"
    assert first.headers["authorization"] == "Bearer tok"
    assert (first.headers["cont-yn"], first.headers["next-key"]) == ("N", "")
    assert json.loads(first.content) == {"x": "1"}
    assert (second.headers["cont-yn"], second.headers["next-key"]) == ("Y", "k1")


def test_a_page_without_the_list_key_contributes_no_rows():
    client = replying([({"return_code": 0}, {})], [])

    rows = fetch_pages(
        client,
        token="tok",
        api_id="ka99999",
        path="thme",
        body={},
        list_key="rows",
        settings=SETTINGS,
    )

    assert rows == []


def test_a_failed_page_raises():
    client = replying([({"return_code": 5, "return_msg": "rate limited"}, {})], [])

    with pytest.raises(RuntimeError, match="ka99999 failed: 5 rate limited"):
        fetch_pages(
            client,
            token="tok",
            api_id="ka99999",
            path="thme",
            body={},
            list_key="rows",
            settings=SETTINGS,
        )
