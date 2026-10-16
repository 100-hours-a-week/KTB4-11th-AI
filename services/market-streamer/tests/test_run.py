import asyncio
import json
from datetime import datetime

import pytest
from kiwoom import KiwoomWebSocketClient
from ktb_core.krx import KST
from market_streamer import stream
from market_streamer.settings import Settings

LOGIN_OK = {"trnm": "LOGIN", "return_code": 0, "return_msg": "ok"}
REG_OK = {"trnm": "REG", "return_code": 0, "return_msg": "ok"}
REG_REJECTED = {"trnm": "REG", "return_code": 1, "return_msg": "rejected"}
REG_OK_STRING = {"trnm": "REG", "return_code": "0", "return_msg": "ok"}
REAL = {
    "trnm": "REAL",
    "data": [
        {
            "type": "0B",
            "item": "005930",
            "values": {
                "10": "-82000",
                "11": "-500",
                "12": "-0.61",
                "13": "100",
                "14": "100000",
                "20": "090123",
            },
        }
    ],
}
ORDERBOOK = {
    "type": "0D",
    "item": "005930",
    "values": {
        "21": "090124",
        **{str(fid): str(100000 - fid) for fid in range(41, 51)},
        **{str(fid): str(fid) for fid in range(51, 61)},
        **{str(fid): str(fid) for fid in range(61, 71)},
        **{str(fid): str(fid) for fid in range(71, 81)},
    },
}


class FakeConnectionClosedOK(Exception):
    pass


class FakeSocket:
    def __init__(self, frames):
        self.frames = [json.dumps(frame) for frame in frames]
        self.sent = []

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        if not self.frames:
            raise FakeConnectionClosedOK()
        return self.frames.pop(0)

    async def close(self):
        pass


class FakeAuth:
    mode = "real"

    def get_access_token(self):
        return "token"

    def recover_from_auth_failure(self):
        pass


class FakePipeline:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def delete(self, key):
        pass

    async def execute(self):
        pass


def _client(sockets):
    pending = list(sockets)

    async def connect(uri, timeout):
        if not pending:
            raise asyncio.CancelledError
        return pending.pop(0)

    return KiwoomWebSocketClient(FakeAuth(), connect_factory=connect)


def _record(monkeypatch):
    published, books, delays = [], [], []

    async def fake_publish(valkey, prices):
        published.append(prices)

    async def fake_sleep(delay):
        delays.append(delay)

    async def fake_publish_orderbooks(valkey, orderbooks):
        books.append(orderbooks)

    monkeypatch.setattr(stream, "publish", fake_publish)
    monkeypatch.setattr(stream, "publish_orderbooks", fake_publish_orderbooks)
    monkeypatch.setattr(stream.asyncio, "sleep", fake_sleep)
    return published, books, delays


def test_stream_publishes_ticks_and_registers_again_after_disconnect(monkeypatch):
    published, books, delays = _record(monkeypatch)
    mixed = {"trnm": "REAL", "data": [REAL["data"][0], ORDERBOOK]}
    first, second = FakeSocket([LOGIN_OK, REG_OK, mixed]), FakeSocket([LOGIN_OK, REG_OK])
    codes = ["000660", "005930"]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stream.stream_market_data(_client([first, second]), object(), codes))

    for socket in (first, second):
        assert socket.sent[0] == {"trnm": "LOGIN", "token": "token"}
        assert socket.sent[1]["trnm"] == "REG"
        assert socket.sent[1]["data"] == [{"item": codes, "type": ["0B", "0D"]}]
    assert [[price["code"] for price in prices] for prices in published] == [["005930"]]
    assert published[0][0]["timestamp"].endswith("T09:01:23+09:00")
    assert [[book["code"] for book in orderbooks] for orderbooks in books] == [["005930"]]
    assert books[0][0]["timestamp"].endswith("T09:01:24+09:00")
    assert books[0][0]["asks"][0]["price"] == abs(100000 - 41)
    assert delays == [1.0, 1.0]


def test_malformed_orderbook_does_not_suppress_valid_tick(monkeypatch):
    published, books, _ = _record(monkeypatch)
    malformed = {**ORDERBOOK, "values": {"21": "bad"}}
    frame = {"trnm": "REAL", "data": [malformed, REAL["data"][0]]}

    with pytest.raises(asyncio.CancelledError):
        client = _client([FakeSocket([LOGIN_OK, REG_OK, frame])])
        asyncio.run(stream.stream_market_data(client, object(), ["005930"]))

    assert [[price["code"] for price in prices] for prices in published] == [["005930"]]
    assert books == []


def test_stream_publishes_real_entries_in_arrival_order(monkeypatch):
    _record(monkeypatch)
    published = []

    async def record_prices(valkey, prices):
        published.extend(("price", price["code"]) for price in prices)

    async def record_orderbooks(valkey, orderbooks):
        published.extend(("orderbook", orderbook["code"]) for orderbook in orderbooks)

    monkeypatch.setattr(stream, "publish", record_prices)
    monkeypatch.setattr(stream, "publish_orderbooks", record_orderbooks)
    second_orderbook = {**ORDERBOOK, "item": "000660"}
    frame = {"trnm": "REAL", "data": [ORDERBOOK, REAL["data"][0], second_orderbook]}

    with pytest.raises(asyncio.CancelledError):
        client = _client([FakeSocket([LOGIN_OK, REG_OK, frame])])
        asyncio.run(stream.stream_market_data(client, object(), ["000660", "005930"]))

    assert published == [
        ("orderbook", "005930"),
        ("price", "005930"),
        ("orderbook", "000660"),
    ]


def test_rejected_registration_backs_off_exponentially(monkeypatch):
    _, _, delays = _record(monkeypatch)
    sockets = [FakeSocket([LOGIN_OK, REG_REJECTED]) for _ in range(6)]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stream.stream_market_data(_client(sockets), object(), ["005930"]))

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]


def test_registration_ack_with_string_return_code_resets_backoff(monkeypatch):
    _, _, delays = _record(monkeypatch)
    sockets = [
        FakeSocket([LOGIN_OK, REG_REJECTED]),
        FakeSocket([LOGIN_OK, REG_OK_STRING]),
        FakeSocket([LOGIN_OK]),
    ]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stream.stream_market_data(_client(sockets), object(), ["005930"]))

    assert delays == [1.0, 1.0, 2.0]


def _set_env(monkeypatch):
    monkeypatch.setenv("MARKET_STREAMER_POSTGRES_DSN", "postgresql+psycopg://x@localhost/x")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_APP_KEY", "app")
    monkeypatch.setenv("MARKET_STREAMER_KIWOOM_SECRET_KEY", "secret")
    monkeypatch.setenv("MARKET_STREAMER_VALKEY_HOST", "localhost")


def test_run_builds_the_client_with_the_keepalive_connect_factory(monkeypatch):
    _set_env(monkeypatch)
    built = []

    class RecordingClient:
        def __init__(self, auth, **kwargs):
            built.append(kwargs)

        async def close(self):
            pass

    async def forever(client, valkey, codes):
        await asyncio.Event().wait()

    class FakeValkey:
        async def ping(self):
            return True

        def pipeline(self, **kwargs):
            return FakePipeline()

        async def aclose(self):
            pass

    monkeypatch.setattr(stream, "KiwoomWebSocketClient", RecordingClient)
    monkeypatch.setattr(stream, "load_codes", lambda dsn, index_name: ["005930"])
    monkeypatch.setattr(stream, "connect_valkey", lambda settings: FakeValkey())
    monkeypatch.setattr(stream, "stream_market_data", forever)

    asyncio.run(stream.run(Settings(), datetime(2026, 10, 13, 16, 0, tzinfo=KST)))

    assert built == [{"connect_factory": stream.connect_kiwoom}]


def test_run_fails_when_valkey_is_unreachable_and_still_closes_it(monkeypatch):
    _set_env(monkeypatch)
    closed = []

    class FakeValkey:
        async def ping(self):
            raise ConnectionError("denied")

        async def aclose(self):
            closed.append("valkey")

    monkeypatch.setattr(stream, "load_codes", lambda dsn, index_name: ["005930"])
    monkeypatch.setattr(stream, "connect_valkey", lambda settings: FakeValkey())

    with pytest.raises(ConnectionError, match="denied"):
        asyncio.run(stream.run(Settings(), datetime(2026, 10, 13, 16, 0, tzinfo=KST)))

    assert closed == ["valkey"]


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 13, 15, 39, 59, 900000, tzinfo=KST),
        datetime(2026, 10, 13, 16, 0, tzinfo=KST),
    ],
)
def test_run_exits_cleanly_at_the_stop_time(monkeypatch, now):
    _set_env(monkeypatch)
    closed = []

    class FakeValkey:
        async def ping(self):
            return True

        def pipeline(self, **kwargs):
            return FakePipeline()

        async def aclose(self):
            closed.append("valkey")

    async def forever(client, valkey, codes):
        await asyncio.Event().wait()

    monkeypatch.setattr(stream, "load_codes", lambda dsn, index_name: ["005930"])
    monkeypatch.setattr(stream, "connect_valkey", lambda settings: FakeValkey())
    monkeypatch.setattr(stream, "stream_market_data", forever)

    asyncio.run(stream.run(Settings(), now))

    assert closed == ["valkey"]
