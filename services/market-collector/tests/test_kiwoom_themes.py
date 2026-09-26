import pytest
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.rest import RATE_LIMITED, KiwoomRequestError
from market_collector.kiwoom.themes import ThemeClient
from market_collector.settings import KiwoomAccount

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")
TOKEN_OK = {"return_code": 0, "token": "t1", "token_type": "Bearer", "expires_dt": "20270101000000"}


GROUP = {
    "thema_grp_cd": "103",
    "thema_nm": "태양광_발전/설치/운영",
    "stk_num": "3",
    "flu_sig": "5",
    "flu_rt": "-1.20",
    "rising_stk_num": "1",
    "fall_stk_num": "2",
    "dt_prft_rt": "+297.10",
    "main_stk": "에스에너지, 한화솔루션",
}
MEMBER = {
    "stk_cd": "009830",
    "stk_nm": "한화솔루션",
    "cur_prc": "-27550",
    "flu_rt": "-0.18",
    "acc_trde_qty": "949407",
}


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, body, headers):
        self.calls.append((path, dict(body), dict(headers)))
        return self.responses.pop(0)


def _client(*responses):
    transport = FakeTransport(({}, TOKEN_OK), *responses)
    client = ThemeClient(TokenStore(ACCOUNT, transport), transport, sleep=lambda _: None)
    return client, transport


def test_groups_parses_a_single_page():
    body = {"return_code": 0, "thema_grp": [GROUP]}
    client, transport = _client(({"cont-yn": "N"}, body))

    groups = client.groups(date_tp=10)

    assert len(groups) == 1
    group = groups[0]
    assert group.code == "103"
    assert group.name == "태양광_발전/설치/운영"
    assert group.date_tp == 10
    assert group.dt_prft_rt == 297.10
    assert group.change_rate == -1.20
    assert group.stock_count == 3
    assert group.rising_count == 1
    assert group.falling_count == 2
    assert group.main_stocks == "에스에너지, 한화솔루션"

    path, sent, headers = transport.calls[1]
    assert path == "/api/dostk/thme"
    assert headers["api-id"] == "ka90001"
    assert sent["date_tp"] == "10"
    assert sent["qry_tp"] == "0"
    assert sent["stex_tp"] == "1"


def test_change_rate_keeps_its_sign_but_dt_prft_rt_does_too():
    body = {"return_code": 0, "thema_grp": [{**GROUP, "flu_rt": "+1.20", "dt_prft_rt": "-12.50"}]}
    client, _ = _client(({"cont-yn": "N"}, body))

    group = client.groups(date_tp=10)[0]

    assert group.change_rate == 1.20
    assert group.dt_prft_rt == -12.50


def test_groups_follows_continuation_to_the_end():
    page1 = ({"cont-yn": "Y", "next-key": "NK1"}, {"return_code": 0, "thema_grp": [GROUP]})
    page2 = ({"cont-yn": "N"}, {"return_code": 0, "thema_grp": [{**GROUP, "thema_grp_cd": "552"}]})
    client, transport = _client(page1, page2)

    groups = client.groups(date_tp=10)

    assert [g.code for g in groups] == ["103", "552"]
    assert transport.calls[2][2]["next-key"] == "NK1"


def test_members_parses_and_tags_the_theme_code():
    body = {"return_code": 0, "thema_comp_stk": [MEMBER]}
    client, transport = _client(({"cont-yn": "N"}, body))

    members = client.members("557", date_tp=10)

    assert len(members) == 1
    assert members[0].theme_code == "557"
    assert members[0].symbol == "009830"
    assert members[0].stock_name == "한화솔루션"

    _, sent, headers = transport.calls[1]
    assert headers["api-id"] == "ka90002"
    assert sent == {"date_tp": "10", "thema_grp_cd": "557", "stex_tp": "1"}


def test_an_unparseable_rate_becomes_none_rather_than_raising():
    body = {"return_code": 0, "thema_grp": [{**GROUP, "dt_prft_rt": ""}]}
    client, _ = _client(({"cont-yn": "N"}, body))

    assert client.groups(date_tp=10)[0].dt_prft_rt is None


def test_rate_limited_is_shared_with_rest_rather_than_redefined():
    from market_collector.kiwoom.rest import RATE_LIMITED as REST_RATE_LIMITED

    assert RATE_LIMITED is REST_RATE_LIMITED


def test_groups_stops_and_raises_when_next_key_stops_advancing():

    stuck_page = ({"cont-yn": "Y", "next-key": "STUCK"}, {"return_code": 0, "thema_grp": [GROUP]})
    client, transport = _client(stuck_page, stuck_page)

    with pytest.raises(KiwoomRequestError, match="STUCK"):
        client.groups(date_tp=10)

    assert len(transport.calls) == 3


def test_members_stops_and_raises_when_next_key_stops_advancing():
    stuck_page = (
        {"cont-yn": "Y", "next-key": "STUCK"},
        {"return_code": 0, "thema_comp_stk": [MEMBER]},
    )
    client, transport = _client(stuck_page, stuck_page)

    with pytest.raises(KiwoomRequestError, match="STUCK"):
        client.members("557", date_tp=10)

    assert len(transport.calls) == 3
