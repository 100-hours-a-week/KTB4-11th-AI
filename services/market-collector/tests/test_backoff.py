import pytest
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.rest import ChartClient, KiwoomRateLimited, KiwoomRequestError
from market_collector.kiwoom.themes import ThemeClient
from market_collector.settings import KiwoomAccount

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")
TOKEN_OK = {"return_code": 0, "token": "t1", "token_type": "Bearer", "expires_dt": "20270101000000"}
LIMITED = ({}, {"return_code": 5, "return_msg": "허용된 요청 개수를 초과하였습니다"})
MINUTE_ROW = {
    "cntr_tm": "20260922151900",
    "cur_prc": "+277500",
    "open_pric": "+277750",
    "high_pric": "+278000",
    "low_pric": "+277500",
    "trde_qty": "1000",
}
CHART_OK = ({"cont-yn": "N"}, {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]})
GROUP_OK = (
    {"cont-yn": "N"},
    {
        "return_code": 0,
        "thema_grp": [
            {
                "thema_grp_cd": "103",
                "thema_nm": "t",
                "stk_num": "1",
                "flu_rt": "0.0",
                "rising_stk_num": "1",
                "fall_stk_num": "0",
                "dt_prft_rt": "1.0",
                "main_stk": "a",
            }
        ],
    },
)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, path, body, headers):
        self.calls += 1
        return self.responses.pop(0)


def test_a_rate_limited_response_is_retried_and_then_succeeds():
    transport = FakeTransport(({}, TOKEN_OK), LIMITED, LIMITED, CHART_OK)
    slept = []

    client = ChartClient(
        TokenStore(ACCOUNT, transport),
        transport,
        interval=0.0,
        sleep=slept.append,
        backoff_base=2.0,
    )

    page = client.minute_page("005930", 1)

    assert len(page.rows) == 1
    assert [wait for wait in slept if wait] == [2.0, 4.0]


def test_backoff_gives_up_after_max_retries_and_raises():
    transport = FakeTransport(({}, TOKEN_OK), LIMITED, LIMITED, LIMITED)
    client = ChartClient(
        TokenStore(ACCOUNT, transport), transport, sleep=lambda _: None, max_retries=2
    )

    with pytest.raises(KiwoomRateLimited):
        client.minute_page("005930", 1)

    assert transport.calls == 4


def test_a_non_rate_limit_error_is_not_retried():
    bad = ({}, {"return_code": 2, "return_msg": "입력 값 오류입니다"})
    transport = FakeTransport(({}, TOKEN_OK), bad)
    client = ChartClient(TokenStore(ACCOUNT, transport), transport, sleep=lambda _: None)

    with pytest.raises(KiwoomRequestError):
        client.minute_page("005930", 1)

    assert transport.calls == 2


def test_the_theme_client_backs_off_the_same_way():
    transport = FakeTransport(({}, TOKEN_OK), LIMITED, GROUP_OK)
    slept = []
    client = ThemeClient(
        TokenStore(ACCOUNT, transport),
        transport,
        interval=0.0,
        sleep=slept.append,
        backoff_base=3.0,
    )

    groups = client.groups(date_tp=10)

    assert len(groups) == 1
    assert [wait for wait in slept if wait] == [3.0]
