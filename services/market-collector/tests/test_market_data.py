from contextlib import nullcontext
from datetime import datetime

import pytest
from kiwoom import Continuation, KiwoomResponse
from ktb_core.krx import KST
from market_collector import __main__ as cli
from market_collector.kiwoom.official import ChartClient
from market_collector.settings import Settings
from redis.cluster import key_slot


class Client:
    def __init__(self):
        self.calls = []

    def fetch_page(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["api_id"] == "ka20001":
            return KiwoomResponse(
                {"cur_prc": "+2750.12", "pred_pre": "-12.34", "flu_rt": "-0.45"},
                Continuation(False, None, "N"),
                {},
            )
        raise AssertionError(f"unexpected Kiwoom request: {kwargs}")


def test_index_quote_uses_kiwoom_sector_price_fields():
    client = Client()

    quote = ChartClient(client).index_quote("KOSPI200")

    assert quote == {"value": 2750.12, "change": -12.34, "rate": -0.45}
    assert client.calls == [
        {
            "api_id": "ka20001",
            "path": "/api/dostk/sect",
            "body": {"mrkt_tp": "2", "inds_cd": "201"},
        }
    ]


def test_archive_queries_both_indices_after_each_20_stocks_then_popularity(monkeypatch):
    events = []

    class Connection:
        active = False

        def __enter__(self):
            self.active = True
            return self

        def __exit__(self, *args):
            self.active = False

    db = Connection()
    now = datetime(2026, 10, 15, 10, 0, tzinfo=KST)

    class Store:
        def latest_bar_timestamps(self, symbols):
            return {}

    class MarketClient:
        def index_quote(self, name):
            events.append(name)
            return {"value": 100.0, "change": 1.0, "rate": 1.0}

    class Valkey:
        def __init__(self):
            self.values = {}
            self.ranks = {}
            self.transactions = []
            self.executions = 0

        def set(self, key, value):
            self.values[key] = value
            if self.transactions:
                self.transactions[-1].append(key)

        def pipeline(self, transaction):
            assert transaction is True
            self.transactions.append([])
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def delete(self, key):
            self.transactions[-1].append(key)
            self.ranks.pop(key, None)

        def zadd(self, key, scores):
            self.transactions[-1].append(key)
            self.ranks[key] = scores

        def execute(self):
            self.executions += 1

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: db)
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(
        cli,
        "reconcile_candles",
        lambda client, store, symbol, timeframe, base_dt, latest: events.append(symbol) or 1,
    )
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_CONF", "unused")
    monkeypatch.setenv("MARKET_COLLECTOR_POSTGRES_DSN", "unused")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_APP_KEY", "app")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_SECRET_KEY", "secret")
    monkeypatch.setenv("MARKET_COLLECTOR_VALKEY_HOST", "localhost")
    symbols = [f"{index:06d}" for index in range(100)]

    cache = {}

    def popularity(connection, loaded_symbols, at, session_cache):
        assert session_cache is cache
        assert connection is db
        assert db.active
        assert loaded_symbols is symbols
        assert at is now
        events.append("popular")
        return symbols[::-1]

    monkeypatch.setattr(cli, "popular_codes", popularity, raising=False)
    valkey = Valkey()

    written = cli.collect_market_data(
        Settings(), symbols, MarketClient(), valkey, now, False, cache
    )

    assert written == 100
    expected = []
    for index, symbol in enumerate(symbols, 1):
        expected.append(symbol)
        if index % 20 == 0:
            expected.extend(["KOSPI", "KOSPI200"])
    assert events == expected + ["popular"]
    assert valkey.ranks["ranking:{popular}"] == {
        code: 100 - index for index, code in enumerate(symbols[::-1])
    }
    assert valkey.executions == 1
    assert len(valkey.transactions) == 1
    assert valkey.transactions[0] == [
        "ranking:{popular}",
        "ranking:{popular}",
        "ranking:{popular}:updated_at",
    ]
    datetime.fromisoformat(valkey.values["ranking:{popular}:updated_at"])
    assert key_slot(b"ranking:{popular}") == key_slot(b"ranking:{popular}:updated_at")
    assert "index:KOSPI" in valkey.values
    assert "index:KOSPI200" in valkey.values


@pytest.mark.parametrize("failure", [False, True])
def test_empty_or_failed_popularity_keeps_previous_values_and_updates_indices(
    monkeypatch, caplog, failure
):
    class Store:
        def latest_bar_timestamps(self, symbols):
            return {}

    class MarketClient:
        def index_quote(self, name):
            return {"value": 100.0, "change": 1.0, "rate": 1.0}

    class Valkey:
        def __init__(self):
            self.values = {"ranking:{popular}": "old", "ranking:{popular}:updated_at": "old-time"}

        def set(self, key, value):
            self.values[key] = value

    def popularity(*args):
        if failure:
            raise ConnectionError("QuestDB unavailable")
        return []

    monkeypatch.setattr(cli, "popular_codes", popularity, raising=False)
    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(object()))
    monkeypatch.setattr(cli, "Store", lambda db: Store())
    monkeypatch.setattr(cli, "reconcile_candles", lambda *args: 1)
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_CONF", "unused")
    monkeypatch.setenv("MARKET_COLLECTOR_POSTGRES_DSN", "unused")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_APP_KEY", "app")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_SECRET_KEY", "secret")
    monkeypatch.setenv("MARKET_COLLECTOR_VALKEY_HOST", "localhost")
    valkey = Valkey()
    written = cli.collect_market_data(
        Settings(),
        [f"{index:06d}" for index in range(20)],
        MarketClient(),
        valkey,
        datetime(2026, 10, 15, 10, 0, tzinfo=KST),
        False,
    )
    assert written == 20
    assert valkey.values["ranking:{popular}"] == "old"
    assert valkey.values["ranking:{popular}:updated_at"] == "old-time"
    assert "index:KOSPI" in valkey.values
    assert "index:KOSPI200" in valkey.values
    errors = [record for record in caplog.records if record.getMessage() == "popular_rank_failed"]
    assert len(errors) == int(failure)
