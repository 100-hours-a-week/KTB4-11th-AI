from contextlib import nullcontext
from datetime import UTC, datetime, timedelta, timezone

import pytest
from kiwoom import Continuation, KiwoomResponse
from market_collector.store import Store
from market_collector.universe import (
    MEMBERS_API_ID,
    UNIVERSE_MEMBERS_TABLE,
    EmptyUniverseError,
    IndexClient,
    IndexMember,
    fetch_members,
    upsert_members,
)

TS = datetime(2026, 9, 25, 6, 0, tzinfo=UTC)


ROW = {"stk_cd": "005930", "stk_nm": "삼성전자"}
ALPHANUMERIC_ROW = {"stk_cd": "0126Z0", "stk_nm": "삼성에피스홀딩스"}


class FakeClient:
    def __init__(self, *responses):
        self.responses = responses
        self.calls = []

    def iterate_pages(self, **kwargs):
        self.calls.append(kwargs)
        yield from self.responses


class FakeSink:
    def __init__(self):
        self.rows = []
        self.flushes = 0

    def row(self, table, *, symbols, columns, at):
        self.rows.append((table, dict(symbols), dict(columns), at))

    def flush(self):
        self.flushes += 1

    def sender(self):
        return nullcontext(self)


def _response(rows, next_key=None):
    return KiwoomResponse(
        {"return_code": 0, "inds_stkpc": rows},
        Continuation(next_key is not None, next_key, "Y" if next_key else "N"),
        {},
    )


def _member(symbol="005930", index_code="201", stock_name="삼성전자"):
    return IndexMember(index_code=index_code, symbol=symbol, stock_name=stock_name)


def test_members_parses_a_single_page():
    official = FakeClient(_response([ROW]))
    client = IndexClient(official, interval=0)

    members = client.members("201")

    assert len(members) == 1
    assert members[0] == IndexMember(index_code="201", symbol="005930", stock_name="삼성전자")

    call = official.calls[0]
    assert call["path"] == "/api/dostk/sect"
    assert call["api_id"] == MEMBERS_API_ID
    assert call["body"] == {"mrkt_tp": "0", "inds_cd": "201", "stex_tp": "1"}


def test_members_follows_continuation_to_the_end():
    client = IndexClient(
        FakeClient(
            _response([ROW], "NK1"),
            _response([{**ROW, "stk_cd": "000660", "stk_nm": "SK하이닉스"}]),
        ),
        interval=0,
    )

    members = client.members("201")

    assert [m.symbol for m in members] == ["005930", "000660"]


def test_members_stops_and_raises_when_next_key_stops_advancing():
    client = IndexClient(FakeClient(_response([ROW], "STUCK"), _response([ROW], "STUCK")), 0)

    with pytest.raises(RuntimeError, match="STUCK"):
        client.members("201")


def test_fetch_members_accepts_alphanumeric_codes_that_are_not_six_digits():

    client = IndexClient(FakeClient(_response([ALPHANUMERIC_ROW])), 0)

    assert fetch_members(client, "201")[0].symbol == "0126Z0"


def test_fetch_members_rejects_a_symbol_that_is_not_six_alphanumeric_characters():
    body = {"return_code": 0, "inds_stkpc": [{"stk_cd": "5930", "stk_nm": "bad"}]}
    client = IndexClient(FakeClient(_response(body["inds_stkpc"])), 0)

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


def test_upsert_members_writes_one_row_per_member():
    sink = FakeSink()

    written = upsert_members(Store(sink), TS, "201", [_member("005930"), _member("000660")])

    assert written == 2
    assert {row[1]["symbol"] for row in sink.rows} == {"005930", "000660"}
    assert all(row[0] == UNIVERSE_MEMBERS_TABLE for row in sink.rows)
    assert sink.flushes == 1


def test_upsert_members_records_the_source_and_the_index_name():
    sink = FakeSink()

    upsert_members(Store(sink), TS, "201", [_member(index_code="201")])

    assert sink.rows[0][1]["src"] == "ka20002"
    assert sink.rows[0][1]["index_name"] == "KOSPI200"


def test_upsert_members_truncates_the_timestamp_to_the_day():

    sink = FakeSink()

    upsert_members(Store(sink), datetime(2026, 9, 25, 6, 0, tzinfo=UTC), "201", [_member()])
    upsert_members(Store(sink), datetime(2026, 9, 25, 23, 59, tzinfo=UTC), "201", [_member()])

    assert sink.rows[0][3] == sink.rows[1][3] == datetime(2026, 9, 25, tzinfo=UTC)


def test_upsert_members_truncates_a_non_utc_timestamp_after_converting():
    sink = FakeSink()
    kst = timezone(timedelta(hours=9))

    upsert_members(Store(sink), datetime(2026, 9, 26, 0, 30, tzinfo=kst), "201", [_member()])

    assert sink.rows[0][3] == datetime(2026, 9, 25, tzinfo=UTC)


def test_upsert_members_raises_rather_than_writing_an_empty_snapshot():
    sink = FakeSink()

    with pytest.raises(EmptyUniverseError):
        upsert_members(Store(sink), TS, "201", [])

    assert sink.rows == []
    assert sink.flushes == 0
