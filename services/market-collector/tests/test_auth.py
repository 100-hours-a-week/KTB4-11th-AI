from datetime import UTC, datetime, timedelta

import pytest
from market_collector.kiwoom.auth import KiwoomAuthError, TokenStore
from market_collector.settings import KiwoomAccount

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, body, headers):
        self.calls.append((path, body, headers))
        return {}, self.responses.pop(0)


def _ok(token, expires):
    return {
        "return_code": 0,
        "return_msg": "정상적으로 처리되었습니다",
        "token": token,
        "token_type": "Bearer",
        "expires_dt": expires,
    }


def test_issues_a_token_and_sends_the_credentials():
    transport = FakeTransport(_ok("t1", "20260923000000"))
    store = TokenStore(ACCOUNT, transport, now=lambda: datetime(2026, 9, 22, 6, 0, tzinfo=UTC))

    assert store.token() == "t1"
    path, body, _ = transport.calls[0]
    assert path == "/oauth2/token"
    assert body == {"grant_type": "client_credentials", "appkey": "k", "secretkey": "s"}


def test_reuses_the_token_until_it_nears_expiry():
    transport = FakeTransport(_ok("t1", "20260923000000"))
    store = TokenStore(ACCOUNT, transport, now=lambda: datetime(2026, 9, 22, 6, 0, tzinfo=UTC))

    assert store.token() == "t1"
    assert store.token() == "t1"
    assert len(transport.calls) == 1


def test_refreshes_before_expiry_rather_than_after():
    clock = [datetime(2026, 9, 22, 6, 0, tzinfo=UTC)]
    transport = FakeTransport(_ok("t1", "20260922160000"), _ok("t2", "20260923160000"))
    store = TokenStore(ACCOUNT, transport, now=lambda: clock[0])

    assert store.token() == "t1"

    clock[0] = datetime(2026, 9, 22, 6, 55, tzinfo=UTC)

    assert store.token() == "t2"
    assert len(transport.calls) == 2


def test_an_unregistered_ip_fails_immediately_and_says_so():
    body = {
        "return_code": 3,
        "return_msg": "인증에 실패했습니다[8050:IP가 등록되지 않았습니다.]",
    }
    store = TokenStore(ACCOUNT, FakeTransport(body))

    with pytest.raises(KiwoomAuthError, match="8050"):
        store.token()


def test_a_wrong_environment_key_fails_immediately():
    body = {
        "return_code": 2,
        "return_msg": "입력 값 오류입니다[8030:투자구분(실전/모의)이 달라서]",
    }
    store = TokenStore(ACCOUNT, FakeTransport(body))

    with pytest.raises(KiwoomAuthError, match="8030"):
        store.token()


def test_expiry_margin_is_five_minutes():
    assert TokenStore.REFRESH_MARGIN == timedelta(minutes=5)
