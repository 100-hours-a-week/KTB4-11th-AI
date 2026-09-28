import json

import httpx
from news_graph_builder.company import fetch_kospi
from news_graph_builder.kiwoom import KiwoomSettings

SETTINGS = KiwoomSettings(
    kiwoom_app_key="app",
    kiwoom_secret_key="secret",
    kiwoom_base_uri="https://kiwoom.test",
    kiwoom_request_interval=0,
)


def test_fetch_kospi_lists_code_and_name_from_ka10099():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        rows = [{"code": "005930", "name": "삼성전자"}, {"code": "000660", "name": "SK하이닉스"}]
        return httpx.Response(200, json={"return_code": 0, "list": rows})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    kospi = fetch_kospi(client, token="tok", settings=SETTINGS)

    assert kospi == [("005930", "삼성전자"), ("000660", "SK하이닉스")]
    assert seen[0].url.path == "/api/dostk/stkinfo"
    assert seen[0].headers["api-id"] == "ka10099"
    assert json.loads(seen[0].content) == {"mrkt_tp": "0"}
