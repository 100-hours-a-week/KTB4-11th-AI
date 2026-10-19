from kiwoom import Continuation, KiwoomClient, KiwoomResponse
from kiwoom.core.secrets import StaticSecretProvider
from kiwoom.core.token_store import MemoryTokenStore
from market_collector.kiwoom import official
from market_collector.kiwoom.official import ChartClient, build_auth, build_client


def test_auth_keeps_credentials_and_tokens_in_memory():
    auth = build_auth("app", "secret", "demo")

    assert auth.mode == "demo"
    assert isinstance(auth.secret_provider, StaticSecretProvider)
    assert isinstance(auth.token_store, MemoryTokenStore)


def test_build_client_returns_the_official_runtime():
    client = build_client("app", "secret", "real")

    assert isinstance(client, KiwoomClient)


class FakeClient:
    def __init__(self):
        self.calls = []

    def fetch_page(self, **kwargs):
        self.calls.append(kwargs)
        return KiwoomResponse(
            {"stk_min_pole_chart_qry": [{"cntr_tm": "20260928090000"}]},
            Continuation(True, "NK1", "Y"),
            {},
        )


def test_chart_page_uses_official_continuation_and_preserves_page_shape():
    official = FakeClient()

    page = ChartClient(official).minute_page("005930", 15, "OLD")

    assert page.next_key == "NK1"
    assert page.has_more is True
    assert official.calls[0] == {
        "api_id": "ka10080",
        "path": "/api/dostk/chart",
        "body": {"stk_cd": "005930", "tic_scope": "15", "upd_stkpc_tp": "1"},
        "cont_yn": "Y",
        "next_key": "OLD",
    }


def test_chart_client_spaces_requests_by_its_interval(monkeypatch):
    client = FakeClient()
    clock = Clock()
    monkeypatch.setattr(official, "time", clock, raising=False)

    chart = ChartClient(client, interval=0.2)
    chart.minute_page("005930", 1)
    chart.minute_page("005930", 1, "NK1")

    assert clock.sleeps == [0.2]


def test_chart_client_never_exceeds_five_requests_per_second(monkeypatch):
    client = FakeClient()
    clock = Clock()
    monkeypatch.setattr(official, "time", clock, raising=False)

    chart = ChartClient(client, interval=0)
    chart.minute_page("005930", 1)
    chart.minute_page("005930", 1, "NK1")

    assert clock.sleeps == [0.2]


def test_stock_info_uses_ka10001_and_shared_request_pacing(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = []

        def fetch_page(self, **kwargs):
            self.calls.append(kwargs)
            return KiwoomResponse(
                {"base_pric": "+100", "return_code": 0}, Continuation(False, None, "N"), {}
            )

    client = Client()
    clock = Clock()
    monkeypatch.setattr(official, "time", clock, raising=False)
    chart = ChartClient(client)
    assert chart.stock_info("005930")["base_pric"] == "+100"
    assert chart.stock_info("000660")["base_pric"] == "+100"
    assert clock.sleeps == [0.2]
    assert client.calls[0] == {
        "api_id": "ka10001",
        "path": "/api/dostk/stkinfo",
        "body": {"stk_cd": "005930"},
    }


def test_stock_metadata_reads_all_kospi_pages():
    class Client:
        def __init__(self):
            self.calls = []

        def fetch_page(self, **kwargs):
            self.calls.append(kwargs)
            rows = (
                [{"code": "005930", "name": "삼성전자", "upName": "전기전자"}]
                if len(self.calls) == 1
                else [{"code": "000660", "name": "SK하이닉스", "upName": "전기전자"}]
            )
            continuation = (
                Continuation(True, "NEXT", "Y")
                if len(self.calls) == 1
                else Continuation(False, None, "N")
            )
            return KiwoomResponse({"list": rows, "return_code": 0}, continuation, {})

    client = Client()

    assert ChartClient(client).stock_metadata() == {
        "005930": ("삼성전자", "전기전자"),
        "000660": ("SK하이닉스", "전기전자"),
    }
    assert client.calls[0] == {
        "api_id": "ka10099",
        "path": "/api/dostk/stkinfo",
        "body": {"mrkt_tp": "0"},
        "cont_yn": None,
        "next_key": None,
    }
    assert client.calls[1]["next_key"] == "NEXT"


def test_stock_metadata_rejects_api_failure():
    class Client:
        def fetch_page(self, **kwargs):
            return KiwoomResponse(
                {"return_code": -1, "return_msg": "unavailable"},
                Continuation(False, None, "N"),
                {},
            )

    import pytest

    with pytest.raises(RuntimeError, match="unavailable"):
        ChartClient(Client()).stock_metadata()


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay
