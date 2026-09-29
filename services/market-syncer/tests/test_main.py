import logging
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from market_syncer import __main__ as entry
from market_syncer.dart import DartCorporation
from market_syncer.kiwoom import Theme, ThemeMember

SAMSUNG = DartCorporation("00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930")
HYNIX = DartCorporation("00164779", "SK하이닉스", None, "000660")
SECRETS = ("app-SECRET", "secret-SECRET", "dart-SECRET")


@pytest.fixture
def env(monkeypatch, pg_dsn):
    for name, value in {
        "POSTGRES_DSN": pg_dsn,
        "KIWOOM_APP_KEY": SECRETS[0],
        "KIWOOM_SECRET_KEY": SECRETS[1],
        "DART_API_KEY": SECRETS[2],
        "KIWOOM_REQUEST_INTERVAL": "0",
    }.items():
        monkeypatch.setenv(f"MARKET_SYNCER_{name}", value)
    # setup_logging replaces the root handlers, which would detach caplog.
    monkeypatch.setattr(entry, "setup_logging", lambda level, **kwargs: None)


class FakeAuth:
    def __init__(self) -> None:
        self.tokens = 0
        self.error: Exception | None = None

    def get_access_token(self) -> str:
        self.tokens += 1
        if self.error:
            raise self.error
        return "tok"


@pytest.fixture
def market_data(monkeypatch):
    client = SimpleNamespace(auth=FakeAuth())
    dart_keys = []

    def fetch_corp_codes(api_key):
        dart_keys.append(api_key)
        return [SAMSUNG, HYNIX]

    monkeypatch.setattr(entry, "build_client", lambda settings: client)
    monkeypatch.setattr(
        entry,
        "fetch_kospi",
        lambda c, **kwargs: [("005930", "삼성전자"), ("000660", "SK하이닉스"), ("069500", "ETF")],
    )
    monkeypatch.setattr(entry, "fetch_corp_codes", fetch_corp_codes)
    monkeypatch.setattr(entry, "fetch_kospi200_codes", lambda c, **kwargs: {"005930", "999999"})
    monkeypatch.setattr(
        entry, "fetch_themes", lambda c, **kwargs: [Theme("100", "HBM", "삼성전자")]
    )
    monkeypatch.setattr(
        entry,
        "fetch_theme_members",
        lambda c, **kwargs: {
            "100": [ThemeMember("005930", "삼성전자"), ThemeMember("000660", "SK하이닉스")]
        },
    )
    return SimpleNamespace(client=client, dart_keys=dart_keys)


def fail(*args, **kwargs):
    raise RuntimeError("upstream down")


def run() -> int:
    with pytest.raises(SystemExit) as exit_info:
        entry.main()
    return exit_info.value.code


def test_syncs_corporations_index_and_themes(env, engine, query, market_data, caplog):
    caplog.set_level(logging.DEBUG)

    assert run() == 0

    assert query(engine, "SELECT stock_code FROM corporations ORDER BY 1") == [
        ("000660",),
        ("005930",),
    ]
    assert query(engine, "SELECT stock_code, index_name FROM corporation_indices") == [
        ("005930", "KOSPI200")
    ]
    assert query(
        engine, "SELECT theme_code, stock_code, is_major FROM theme_companies ORDER BY 2"
    ) == [("100", "000660", False), ("100", "005930", True)]
    assert market_data.client.auth.tokens == 1
    assert market_data.dart_keys == [SECRETS[2]]
    assert "members=1 skipped=1" in caplog.text


def test_no_secret_is_logged(env, engine, market_data, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(entry, "fetch_themes", fail)

    assert run() == 1

    assert caplog.text
    for secret in SECRETS:
        assert secret not in caplog.text


def test_a_failed_token_exits_1_before_any_kiwoom_call(
    env, engine, query, market_data, monkeypatch
):
    assert run() == 0
    market_data.client.auth.error = RuntimeError("token refused")
    calls = []
    for step in ("fetch_kospi", "fetch_kospi200_codes", "fetch_themes", "fetch_theme_members"):
        monkeypatch.setattr(entry, step, lambda *args, step=step, **kwargs: calls.append(step))

    assert run() == 1

    assert calls == []
    assert query(engine, "SELECT count(*) FROM corporation_indices") == [(1,)]


def test_a_failed_first_sync_exits_before_the_other_steps(env, engine, market_data, monkeypatch):
    later = []
    monkeypatch.setattr(entry, "fetch_corp_codes", fail)
    monkeypatch.setattr(entry, "fetch_kospi200_codes", lambda c, **kwargs: later.append(1))
    monkeypatch.setattr(entry, "fetch_themes", lambda c, **kwargs: later.append(1))

    assert run() == 1

    assert later == []


def test_a_failed_later_corporation_sync_still_runs_the_other_steps_and_exits_1(
    env, engine, query, market_data, monkeypatch
):
    assert run() == 0
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM corporation_indices"))
        conn.execute(sa.text("DELETE FROM theme_companies"))
    monkeypatch.setattr(entry, "fetch_kospi", fail)

    assert run() == 1

    assert query(engine, "SELECT count(*) FROM corporation_indices") == [(1,)]
    assert query(engine, "SELECT count(*) FROM theme_companies") == [(2,)]


@pytest.mark.parametrize("step", ["fetch_kospi200_codes", "fetch_themes", "fetch_theme_members"])
def test_a_failed_step_is_isolated_and_exits_1(env, engine, query, market_data, monkeypatch, step):
    monkeypatch.setattr(entry, step, fail)

    assert run() == 1

    assert query(engine, "SELECT count(*) FROM corporations") == [(2,)]
    index_failed = step == "fetch_kospi200_codes"
    assert query(engine, "SELECT count(*) FROM corporation_indices") == [
        (0,) if index_failed else (1,)
    ]
    assert query(engine, "SELECT count(*) FROM theme_companies") == [(2,) if index_failed else (0,)]


def test_an_empty_index_fetch_keeps_the_old_rows_and_exits_1(
    env, engine, query, market_data, monkeypatch
):
    assert run() == 0
    monkeypatch.setattr(entry, "fetch_kospi200_codes", lambda c, **kwargs: set())

    assert run() == 1

    assert query(engine, "SELECT stock_code FROM corporation_indices") == [("005930",)]


def test_a_second_concurrent_run_exits_without_work(env, engine, query, market_data):
    with engine.connect() as holder:
        holder.execute(sa.text("SELECT pg_advisory_lock(:id)"), {"id": entry.RUN_LOCK})
        try:
            assert run() == 0
        finally:
            holder.execute(sa.text("SELECT pg_advisory_unlock(:id)"), {"id": entry.RUN_LOCK})

    assert market_data.client.auth.tokens == 0
    assert query(engine, "SELECT count(*) FROM corporations") == [(0,)]
