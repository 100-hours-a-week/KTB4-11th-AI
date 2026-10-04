from kiwoom import Continuation, KiwoomClient, KiwoomResponse
from kiwoom.core.secrets import StaticSecretProvider
from kiwoom.core.token_store import MemoryTokenStore
from market_collector.kiwoom import official
from market_collector.kiwoom.official import ChartClient, build_auth, build_client
from market_collector.settings import KiwoomAccount


def test_auth_keeps_credentials_and_tokens_in_memory():
    auth = build_auth(KiwoomAccount(app_key="app", secret_key="secret"), "demo")

    assert auth.mode == "demo"
    assert isinstance(auth.secret_provider, StaticSecretProvider)
    assert isinstance(auth.token_store, MemoryTokenStore)


def test_build_client_returns_the_official_runtime():
    client = build_client(KiwoomAccount(app_key="app", secret_key="secret"), "real")

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


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay
