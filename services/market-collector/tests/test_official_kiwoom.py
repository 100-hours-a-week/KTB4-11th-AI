from kiwoom import Continuation, KiwoomClient, KiwoomResponse
from kiwoom.core.secrets import StaticSecretProvider
from kiwoom.core.token_store import MemoryTokenStore
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
