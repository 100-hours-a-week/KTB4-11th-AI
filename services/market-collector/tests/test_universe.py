import sys
import types
from datetime import UTC, datetime, timedelta, timezone

import pytest
from market_collector.kiwoom.auth import TokenStore
from market_collector.kiwoom.rest import KiwoomRequestError
from market_collector.settings import KiwoomAccount
from market_collector.universe import (
    MEMBERS_API_ID,
    UNIVERSE_MEMBERS_TABLE,
    EmptyUniverseError,
    IndexClient,
    IndexMember,
    fetch_members,
    latest_members,
    upsert_members,
)

ACCOUNT = KiwoomAccount(app_key="k", secret_key="s")
TOKEN_OK = {"return_code": 0, "token": "t1", "token_type": "Bearer", "expires_dt": "20270101000000"}
TS = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)

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


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1


def _client(*responses):
    transport = FakeTransport(({}, TOKEN_OK), *responses)
    return IndexClient(TokenStore(ACCOUNT, transport), transport, sleep=lambda _: None), transport


def _member(symbol="005930", index_code="201", stock_name="삼성전자"):
    return IndexMember(index_code=index_code, symbol=symbol, stock_name=stock_name)


def _fake_psycopg(rows, seen=None):
    class FakeCursor:
        def execute(self, query, params):
            if seen is not None:
                seen["query"], seen["params"] = str(query), params

        def fetchall(self):
            return rows

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return types.SimpleNamespace(connect=lambda dsn: FakeConnection())


# ---------------------------------------------------------------- the client


def test_members_parses_a_single_page():
    client, transport = _client(({"cont-yn": "N"}, {"return_code": 0, "inds_stkpc": [ROW]}))

    members = client.members("201")

    assert len(members) == 1
    assert members[0] == IndexMember(index_code="201", symbol="005930", stock_name="삼성전자")

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
    stuck = ({"cont-yn": "Y", "next-key": "STUCK"}, {"return_code": 0, "inds_stkpc": [ROW]})
    client, transport = _client(stuck, stuck)

    with pytest.raises(KiwoomRequestError, match="STUCK"):
        client.members("201")

    assert len(transport.calls) == 3  # token exchange + two page requests


# ------------------------------------------------------------------ fetching


def test_fetch_members_accepts_alphanumeric_codes_that_are_not_six_digits():
    # 0126Z0 (삼성에피스홀딩스) and 0220W0 (한화머시너리앤서비스홀딩스) are real
    # KOSPI 200 constituents with real chart data -- the old six-digit-numeric
    # validator rejected both. The rule is six characters, alphanumeric.
    client, _ = _client(({"cont-yn": "N"}, {"return_code": 0, "inds_stkpc": [ALPHANUMERIC_ROW]}))

    assert fetch_members(client, "201")[0].symbol == "0126Z0"


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
            return [_member()]

    fake = FakeIndexSource()

    members = fetch_members(fake, "201")

    assert fake.calls == ["201"]
    assert [m.symbol for m in members] == ["005930"]


# ------------------------------------------------------------------- writing


def test_upsert_members_writes_one_row_per_member():
    sink = FakeSink()

    written = upsert_members(sink, TS, "201", [_member("005930"), _member("000660")])

    assert written == 2
    assert {row[1]["symbol"] for row in sink.rows} == {"005930", "000660"}
    assert all(row[0] == UNIVERSE_MEMBERS_TABLE for row in sink.rows)
    assert sink.flushes == 1


def test_upsert_members_records_the_source_and_the_index_name():
    sink = FakeSink()

    upsert_members(sink, TS, "201", [_member(index_code="201")])

    assert sink.rows[0][1]["src"] == "ka20002"
    assert sink.rows[0][1]["index_name"] == "KOSPI200"


def test_upsert_members_truncates_the_timestamp_to_the_day():
    # Two runs on the same day must produce the same `at` value, because the
    # QuestDB dedup key is (ts, index_code, symbol) -- this is the precondition
    # that makes a same-day rerun an upsert rather than a second snapshot.
    sink = FakeSink()

    upsert_members(sink, datetime(2026, 9, 25, 6, 0, tzinfo=UTC), "201", [_member()])
    upsert_members(sink, datetime(2026, 9, 25, 23, 59, tzinfo=UTC), "201", [_member()])

    assert sink.rows[0][3] == sink.rows[1][3] == datetime(2026, 9, 25, tzinfo=UTC)


def test_upsert_members_truncates_a_non_utc_timestamp_after_converting():
    sink = FakeSink()
    kst = timezone(timedelta(hours=9))
    # 2026-09-26 00:30 KST is 2026-09-25 15:30 UTC -- truncating in KST first
    # would give the wrong UTC day.
    upsert_members(sink, datetime(2026, 9, 26, 0, 30, tzinfo=kst), "201", [_member()])

    assert sink.rows[0][3] == datetime(2026, 9, 25, tzinfo=UTC)


def test_upsert_members_raises_rather_than_writing_an_empty_snapshot():
    sink = FakeSink()

    with pytest.raises(EmptyUniverseError):
        upsert_members(sink, TS, "201", [])

    assert sink.rows == []
    assert sink.flushes == 0


# ------------------------------------------------------------------- reading


def test_latest_members_returns_a_frozenset_of_symbols(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg([("005930",), ("0126Z0",)], seen))

    result = latest_members("postgresql://localhost:8812/qdb", "201")

    assert result == frozenset({"005930", "0126Z0"})
    assert seen["params"] == ("201", "201")
    assert UNIVERSE_MEMBERS_TABLE in str(seen["query"])


def test_latest_members_raises_empty_universe_error_naming_the_subcommand(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg([]))

    with pytest.raises(EmptyUniverseError, match="universe"):
        latest_members("postgresql://localhost:8812/qdb", "201")
