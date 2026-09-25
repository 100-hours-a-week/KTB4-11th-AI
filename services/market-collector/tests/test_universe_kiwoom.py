import pytest
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.rest import KiwoomRequestError
from market_collector.settings import KiwoomAccount
from market_collector.universe.dto import IndexMember
from market_collector.universe.kiwoom import MEMBERS_API_ID, IndexClient, fetch_members

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")
TOKEN_OK = {"return_code": 0, "token": "t1", "token_type": "Bearer", "expires_dt": "20270101000000"}

# Verbatim shape from the live API on 2026-09-25 (ka20002, inds_cd=201).
ROW = {"stk_cd": "005930", "stk_nm": "삼성전자"}
ALPHANUMERIC_ROW = {"stk_cd": "0126Z0", "stk_nm": "삼성에피스홀딩스"}


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, body, headers):
        self.calls.append((path, dict(body), dict(headers)))
        return self.responses.pop(0)


def _client(*responses):
    transport = FakeTransport(({}, TOKEN_OK), *responses)
    client = IndexClient(TokenStore(ACCOUNT, transport), transport, sleep=lambda _: None)
    return client, transport


def test_members_parses_a_single_page():
    body = {"return_code": 0, "inds_stkpc": [ROW]}
    client, transport = _client(({"cont-yn": "N"}, body))

    members = client.members("201")

    assert len(members) == 1
    member = members[0]
    assert member.index_code == "201"
    assert member.symbol == "005930"
    assert member.stock_name == "삼성전자"

    path, sent, headers = transport.calls[1]
    assert path == "/api/dostk/sect"
    assert headers["api-id"] == MEMBERS_API_ID
    assert sent == {"mrkt_tp": "0", "inds_cd": "201", "stex_tp": "1"}


def test_members_follows_continuation_to_the_end():
    page1 = ({"cont-yn": "Y", "next-key": "NK1"}, {"return_code": 0, "inds_stkpc": [ROW]})
    page2 = (
        {"cont-yn": "N"},
        {"return_code": 0, "inds_stkpc": [{**ROW, "stk_cd": "000660", "stk_nm": "SK하이닉스"}]},
    )
    client, transport = _client(page1, page2)

    members = client.members("201")

    assert [m.symbol for m in members] == ["005930", "000660"]
    assert transport.calls[2][2]["next-key"] == "NK1"


def test_members_stops_and_raises_when_next_key_stops_advancing():
    stuck_page = ({"cont-yn": "Y", "next-key": "STUCK"}, {"return_code": 0, "inds_stkpc": [ROW]})
    client, transport = _client(stuck_page, stuck_page)

    with pytest.raises(KiwoomRequestError, match="STUCK"):
        client.members("201")

    assert len(transport.calls) == 3  # token exchange + two page requests


def test_fetch_members_accepts_alphanumeric_codes_that_are_not_six_digits():
    # 0126Z0 (삼성에피스홀딩스) and 0220W0 (한화머시너리앤서비스홀딩스) are real
    # KOSPI 200 constituents with real chart data -- the old six-digit-numeric
    # validator rejected both. The rule here is six characters, alphanumeric.
    body = {"return_code": 0, "inds_stkpc": [ALPHANUMERIC_ROW]}
    client, _ = _client(({"cont-yn": "N"}, body))

    members = fetch_members(client, "201")

    assert members[0].symbol == "0126Z0"


def test_fetch_members_rejects_a_symbol_that_is_not_six_alphanumeric_characters():
    body = {"return_code": 0, "inds_stkpc": [{"stk_cd": "5930", "stk_nm": "bad"}]}
    client, _ = _client(({"cont-yn": "N"}, body))

    with pytest.raises(ValueError, match="5930"):
        fetch_members(client, "201")


def test_fetch_members_delegates_to_the_clients_members_method():
    class FakeIndexSource:
        def __init__(self):
            self.calls = []

        def members(self, index_code):
            self.calls.append(index_code)
            return [IndexMember(index_code=index_code, symbol="005930", stock_name="삼성전자")]

    fake = FakeIndexSource()

    members = fetch_members(fake, "201")

    assert fake.calls == ["201"]
    assert [m.symbol for m in members] == ["005930"]
