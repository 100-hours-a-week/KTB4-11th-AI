import pytest
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.rest import ChartClient, KiwoomRateLimited, KiwoomRequestError
from market_collector.settings import KiwoomAccount

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")
TOKEN_OK = {
    "return_code": 0,
    "token": "t1",
    "token_type": "Bearer",
    "expires_dt": "20270101000000",
}
MINUTE_ROW = {
    "cur_prc": "+277500",
    "trde_qty": "38961",
    "cntr_tm": "20260922151900",
    "open_pric": "+277750",
    "high_pric": "+278000",
    "low_pric": "+277500",
}


class FakeTransport:
    """Replays scripted (headers, body) pairs and records every call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, body, headers):
        self.calls.append((path, dict(body), dict(headers)))
        return self.responses.pop(0)


def _client(*chart_responses, interval=1.3, max_retries=5):
    transport = FakeTransport(({}, TOKEN_OK), *chart_responses)
    slept = []
    client = ChartClient(
        TokenStore(ACCOUNT, transport),
        transport,
        interval=interval,
        sleep=slept.append,
        max_retries=max_retries,
    )
    return client, transport, slept


def test_minute_page_sends_the_documented_parameters():
    body = {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]}
    client, transport, _ = _client(({"cont-yn": "N"}, body))

    page = client.minute_page("005930", 15)

    assert len(page.rows) == 1
    path, sent, headers = transport.calls[1]
    assert path == "/api/dostk/chart"
    assert sent == {"stk_cd": "005930", "tic_scope": "15", "upd_stkpc_tp": "1"}
    assert headers["api-id"] == "ka10080"
    assert headers["authorization"] == "Bearer t1"


def test_continuation_headers_are_echoed_back_on_the_next_call():
    first = (
        {"cont-yn": "Y", "next-key": "NK1"},
        {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]},
    )
    second = ({"cont-yn": "N"}, {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]})
    client, transport, _ = _client(first, second)

    page1 = client.minute_page("005930", 1)
    assert page1.has_more is True
    assert page1.next_key == "NK1"

    page2 = client.minute_page("005930", 1, next_key=page1.next_key)
    assert page2.has_more is False
    _, _, headers = transport.calls[2]
    assert headers["cont-yn"] == "Y"
    assert headers["next-key"] == "NK1"


def test_the_first_call_does_not_sleep_but_the_second_does():
    body = {"return_code": 0, "stk_min_pole_chart_qry": [MINUTE_ROW]}
    client, _, slept = _client(({"cont-yn": "N"}, body), ({"cont-yn": "N"}, body))

    client.minute_page("005930", 1)
    assert slept == []

    client.minute_page("005930", 1)
    assert slept == [1.3]


def test_daily_page_uses_the_other_api_id_and_array():
    row = {
        "dt": "20260922",
        "cur_prc": "277500",
        "open_pric": "283000",
        "high_pric": "283500",
        "low_pric": "274500",
        "trde_qty": "15620240",
        "trde_prica": "4366136",
    }
    body = {"return_code": 0, "stk_dt_pole_chart_qry": [row]}
    client, transport, _ = _client(({"cont-yn": "N"}, body))

    page = client.daily_page("005930", "20260922")

    assert page.rows == [row]
    _, sent, headers = transport.calls[1]
    assert headers["api-id"] == "ka10081"
    assert sent == {"stk_cd": "005930", "base_dt": "20260922", "upd_stkpc_tp": "1"}


def test_rate_limiting_raises_its_own_error():
    # Retry/backoff behaviour is covered by test_backoff.py; with max_retries=0 this
    # exercises only the return_code=5 -> KiwoomRateLimited mapping on the first attempt.
    body = {"return_code": 5, "return_msg": "허용된 요청 개수를 초과하였습니다"}
    client, _, _ = _client(({}, body), max_retries=0)

    with pytest.raises(KiwoomRateLimited):
        client.minute_page("005930", 1)


def test_any_other_error_code_raises_the_generic_error():
    body = {"return_code": 2, "return_msg": "입력 값 오류입니다"}
    client, _, _ = _client(({}, body))

    with pytest.raises(KiwoomRequestError, match="return_code=2"):
        client.minute_page("005930", 1)


def test_an_exhausted_history_is_an_empty_page_not_an_error():
    body = {"return_code": 0, "stk_min_pole_chart_qry": []}
    client, _, _ = _client(({"cont-yn": "N"}, body))

    page = client.minute_page("005930", 1, next_key="NK9")

    assert page.rows == []
    assert page.has_more is False
