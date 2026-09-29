import pytest
from kiwoom import Continuation, KiwoomResponse
from market_syncer import kiwoom
from market_syncer.kiwoom import (
    Theme,
    ThemeMember,
    build_client,
    fetch_kospi,
    fetch_kospi200_codes,
    fetch_rows,
    fetch_theme_members,
    fetch_themes,
)
from market_syncer.settings import Settings


class FakeClient:
    """Replays one list of (body, next_key) pages per iterate_pages call."""

    def __init__(self, *calls: list[tuple[dict, str | None]]) -> None:
        self.replies = list(calls)
        self.calls: list[dict] = []

    def iterate_pages(self, **kwargs):
        self.calls.append(kwargs)
        for body, next_key in self.replies.pop(0):
            has_next = next_key is not None
            yield KiwoomResponse(
                body, Continuation(has_next, next_key, "Y" if has_next else None), {}
            )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(kiwoom.time, "sleep", sleeps.append)
    return sleeps


def test_build_client_uses_the_mode_and_keys():
    settings = Settings(
        postgres_dsn="postgresql://x",
        kiwoom_app_key="app",
        kiwoom_secret_key="secret",
        kiwoom_mode="demo",
        dart_api_key="dart",
    )

    client = build_client(settings)

    assert client.auth.mode == "demo"
    credentials = client.auth.secret_provider.get_credentials("demo")
    assert (credentials.appkey, credentials.secretkey) == ("app", "secret")


def test_fetch_rows_follows_every_page_and_waits_first(no_sleep):
    client = FakeClient([({"data": [{"n": 1}]}, "k1"), ({"data": [{"n": 2}, {"n": 3}]}, None)])

    rows = fetch_rows(
        client, api_id="ka1", path="/p", body={"a": "b"}, array_field="data", interval=0.5
    )

    assert rows == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert client.calls == [
        {
            "api_id": "ka1",
            "path": "/p",
            "body": {"a": "b"},
            "max_pages": 0,
            "page_delay_seconds": 0.5,
        }
    ]
    assert no_sleep == [0.5]


def test_fetch_rows_treats_a_missing_array_as_empty():
    client = FakeClient([({"return_code": 0}, None)])

    assert (
        fetch_rows(client, api_id="ka1", path="/p", body={}, array_field="data", interval=0) == []
    )


def test_fetch_rows_raises_on_a_repeated_next_key():
    client = FakeClient([({"data": []}, "k1"), ({"data": []}, "k1"), ({"data": []}, None)])

    with pytest.raises(RuntimeError, match="stalled paging"):
        fetch_rows(client, api_id="ka1", path="/p", body={}, array_field="data", interval=0)


def test_fetch_rows_rejects_a_non_list_array():
    client = FakeClient([({"data": {"n": 1}}, None)])

    with pytest.raises(TypeError):
        fetch_rows(client, api_id="ka1", path="/p", body={}, array_field="data", interval=0)


def test_fetch_kospi_lists_code_and_name_from_ka10099():
    rows = [{"code": "005930", "name": "삼성전자"}, {"code": "000660", "name": "SK하이닉스"}]
    client = FakeClient([({"list": rows}, None)])

    assert fetch_kospi(client, interval=0) == [("005930", "삼성전자"), ("000660", "SK하이닉스")]
    assert client.calls[0]["api_id"] == "ka10099"
    assert client.calls[0]["path"] == "/api/dostk/stkinfo"
    assert client.calls[0]["body"] == {"mrkt_tp": "0"}


def test_fetch_kospi200_codes_reads_sector_201_and_cuts_suffixes():
    client = FakeClient([({"inds_stkpc": [{"stk_cd": "005930"}, {"stk_cd": "000660_AL"}]}, None)])

    assert fetch_kospi200_codes(client, interval=0) == {"005930", "000660"}
    assert client.calls[0]["api_id"] == "ka20002"
    assert client.calls[0]["path"] == "/api/dostk/sect"
    assert client.calls[0]["body"] == {"mrkt_tp": "2", "inds_cd": "201", "stex_tp": "1"}


def test_fetch_themes_reads_ka90001():
    rows = [
        {"thema_grp_cd": "100", "thema_nm": "HBM", "main_stk": "SK하이닉스"},
        {"thema_grp_cd": "200", "thema_nm": "2차전지"},
    ]
    client = FakeClient([({"thema_grp": rows}, None)])

    assert fetch_themes(client, interval=0) == [
        Theme("100", "HBM", "SK하이닉스"),
        Theme("200", "2차전지", ""),
    ]
    assert client.calls[0]["api_id"] == "ka90001"
    assert client.calls[0]["path"] == "/api/dostk/thme"
    assert client.calls[0]["body"] == {
        "qry_tp": "0",
        "date_tp": "1",
        "flu_pl_amt_tp": "1",
        "stex_tp": "1",
    }


def test_fetch_theme_members_calls_ka90002_per_theme():
    client = FakeClient(
        [({"thema_comp_stk": [{"stk_cd": "000660_AL", "stk_nm": "SK하이닉스"}]}, None)],
        [({"thema_comp_stk": []}, None)],
    )

    members = fetch_theme_members(client, theme_codes=["100", "200"], interval=0)

    assert members == {"100": [ThemeMember("000660", "SK하이닉스")], "200": []}
    assert [call["api_id"] for call in client.calls] == ["ka90002", "ka90002"]
    assert client.calls[0]["body"] == {"thema_grp_cd": "100", "stex_tp": "1", "date_tp": "1"}
