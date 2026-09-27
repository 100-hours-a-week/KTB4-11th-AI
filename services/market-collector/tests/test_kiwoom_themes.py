import pytest
from kiwoom import Continuation, KiwoomResponse
from market_collector.kiwoom.themes import ThemeClient

GROUP = {
    "thema_grp_cd": "103",
    "thema_nm": "태양광",
    "stk_num": "3",
    "flu_rt": "-1.20",
    "rising_stk_num": "1",
    "fall_stk_num": "2",
    "dt_prft_rt": "+297.10",
    "main_stk": "에스에너지, 한화솔루션",
}
MEMBER = {"stk_cd": "009830", "stk_nm": "한화솔루션"}


def _response(field, rows, next_key=None):
    return KiwoomResponse(
        {field: rows},
        Continuation(next_key is not None, next_key, "Y" if next_key else "N"),
        {},
    )


class FakeClient:
    def __init__(self, *responses):
        self.responses = responses
        self.calls = []

    def iterate_pages(self, **kwargs):
        self.calls.append(kwargs)
        yield from self.responses


def test_groups_use_official_unbounded_paging_and_parse_values():
    official = FakeClient(_response("thema_grp", [GROUP]))

    group = ThemeClient(official, interval=1.3).groups(10)[0]

    assert (group.code, group.change_rate, group.dt_prft_rt) == ("103", -1.2, 297.1)
    assert official.calls[0] == {
        "api_id": "ka90001",
        "path": "/api/dostk/thme",
        "body": {
            "qry_tp": "0",
            "stk_cd": "",
            "thema_nm": "",
            "date_tp": "10",
            "flu_pl_amt_tp": "1",
            "stex_tp": "1",
        },
        "max_pages": 0,
        "page_delay_seconds": 1.3,
    }


def test_members_parse_and_tag_the_theme_code():
    official = FakeClient(_response("thema_comp_stk", [MEMBER]))

    member = ThemeClient(official, 0).members("557", 10)[0]

    assert (member.theme_code, member.symbol, member.stock_name) == ("557", "009830", "한화솔루션")
    assert official.calls[0]["api_id"] == "ka90002"


def test_repeated_continuation_is_rejected():
    client = ThemeClient(
        FakeClient(
            _response("thema_grp", [GROUP], "STUCK"),
            _response("thema_grp", [GROUP], "STUCK"),
        ),
        0,
    )

    with pytest.raises(RuntimeError, match="STUCK"):
        client.groups(10)
