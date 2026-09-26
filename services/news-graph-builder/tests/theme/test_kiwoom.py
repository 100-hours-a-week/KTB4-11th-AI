import json

import httpx
from news_graph_builder.kiwoom import KiwoomSettings
from news_graph_builder.theme import (
    Theme,
    ThemeMember,
    fetch_kospi200_codes,
    fetch_theme_members,
    fetch_themes,
)

SETTINGS = KiwoomSettings(
    kiwoom_app_key="app",
    kiwoom_secret_key="secret",
    kiwoom_base_uri="https://kiwoom.test",
    kiwoom_request_interval=0,
)


def replying(bodies: list[dict], seen: list) -> httpx.Client:
    remaining = list(bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"return_code": 0, **remaining.pop(0)})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_themes_reads_ka90001():
    seen = []
    client = replying(
        [
            {
                "thema_grp": [
                    {"thema_grp_cd": "100", "thema_nm": "HBM", "main_stk": "SK하이닉스"},
                    {"thema_grp_cd": "200", "thema_nm": "2차전지"},
                ]
            }
        ],
        seen,
    )

    themes = fetch_themes(client, token="tok", settings=SETTINGS)

    assert themes == [Theme("100", "HBM", "SK하이닉스"), Theme("200", "2차전지", "")]
    assert seen[0].url.path == "/api/dostk/thme"
    assert seen[0].headers["api-id"] == "ka90001"
    assert json.loads(seen[0].content) == {
        "qry_tp": "0",
        "date_tp": "1",
        "flu_pl_amt_tp": "1",
        "stex_tp": "1",
    }


def test_fetch_kospi200_codes_reads_sector_201_and_cuts_suffixes():
    seen = []
    client = replying([{"inds_stkpc": [{"stk_cd": "005930"}, {"stk_cd": "000660_AL"}]}], seen)

    codes = fetch_kospi200_codes(client, token="tok", settings=SETTINGS)

    assert codes == {"005930", "000660"}
    assert seen[0].url.path == "/api/dostk/sect"
    assert seen[0].headers["api-id"] == "ka20002"
    assert json.loads(seen[0].content) == {"mrkt_tp": "2", "inds_cd": "201", "stex_tp": "1"}


def test_fetch_theme_members_calls_ka90002_per_theme():
    seen = []
    client = replying(
        [
            {"thema_comp_stk": [{"stk_cd": "000660_AL", "stk_nm": "SK하이닉스"}]},
            {"thema_comp_stk": []},
        ],
        seen,
    )

    members = fetch_theme_members(
        client, token="tok", theme_codes=["100", "200"], settings=SETTINGS
    )

    assert members == {"100": [ThemeMember("000660", "SK하이닉스")], "200": []}
    assert [request.headers["api-id"] for request in seen] == ["ka90002", "ka90002"]
    assert json.loads(seen[0].content) == {"thema_grp_cd": "100", "stex_tp": "1", "date_tp": "1"}
