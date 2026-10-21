from contextlib import nullcontext
from datetime import datetime

import pytest
from kiwoom import Continuation, KiwoomResponse
from ktb_core.krx import KST
from market_collector import __main__ as cli
from market_collector.kiwoom import official
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
        return KiwoomResponse(
            {
                "item_inq_rank": [
                    {"stk_cd": "000660", "bigd_rank": "3"},
                    {"stk_cd": "035420", "bigd_rank": "1"},
                    {"stk_cd": "005930", "bigd_rank": "2"},
                ]
            },
            Continuation(False, None, "N"),
            {},
        )


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


def test_popular_codes_keeps_kiwoom_rank_after_kospi200_filter():
    client = Client()

    codes = ChartClient(client).popular_codes({"000660", "005930"})

    assert codes == ["005930", "000660"]
    assert client.calls == [
        {
            "api_id": "ka00198",
            "path": "/api/dostk/stkinfo",
            "body": {"qry_tp": "1"},
        }
    ]


def test_kiwoom_error_response_does_not_become_an_empty_popular_ranking():
    class ErrorClient:
        def fetch_page(self, **kwargs):
            return KiwoomResponse(
                {"return_code": 1, "return_msg": "failed", "item_inq_rank": []},
                Continuation(False, None, "N"),
                {},
            )

    with pytest.raises(RuntimeError, match="failed"):
        ChartClient(ErrorClient()).popular_codes({"005930"})


def test_chart_index_and_popularity_share_request_limiter(monkeypatch):
    clock = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(official.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(official.time, "sleep", sleep)
    client = ChartClient(Client(), interval=1.0)

    client.minute_page("005930", 1)
    client.index_quote("KOSPI")
    client.popular_codes({"005930"})

    assert sleeps == [1.0, 1.0]


def test_archive_queries_both_indices_after_each_20_stocks_then_popularity(monkeypatch):
    events = []

    class Store:
        def latest_bar_timestamps(self, symbols):
            return {}

    class MarketClient:
        def index_quote(self, name):
            events.append(name)
            return {"value": 100.0, "change": 1.0, "rate": 1.0}

        def popular_codes(self, universe):
            events.append("popular")
            return ["000000"]

    class Valkey:
        def __init__(self):
            self.values = {}
            self.ranks = {}

        def set(self, key, value):
            self.values[key] = value

        def pipeline(self, transaction):
            assert transaction is True
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def delete(self, key):
            self.ranks.pop(key, None)

        def zadd(self, key, scores):
            self.ranks[key] = scores

        def execute(self):
            pass

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(object()))
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
    symbols = [f"{index:06d}" for index in range(40)]
    valkey = Valkey()

    written = cli.collect_market_data(
        Settings(), symbols, MarketClient(), valkey, datetime(2026, 10, 15, 9, 0, tzinfo=KST), False
    )

    assert written == 40
    assert events == symbols[:20] + ["KOSPI", "KOSPI200"] + symbols[20:] + [
        "KOSPI",
        "KOSPI200",
        "popular",
    ]
    assert valkey.ranks["ranking:{popular}"] == {"000000": 1}
    assert "ranking:{popular}:updated_at" in valkey.values
    assert key_slot(b"ranking:{popular}") == key_slot(b"ranking:{popular}:updated_at")
    assert "index:KOSPI" in valkey.values
    assert "index:KOSPI200" in valkey.values


def test_failed_index_and_popularity_requests_keep_previous_values(monkeypatch, caplog):
    events = []

    class Store:
        def latest_bar_timestamps(self, symbols):
            return {}

    class MarketClient:
        def index_quote(self, name):
            raise ConnectionError(name)

        def popular_codes(self, universe):
            raise ConnectionError("popular")

    class Valkey:
        def __init__(self):
            self.values = {"index:KOSPI": "old", "ranking:{popular}": "old"}

        def set(self, key, value):
            self.values[key] = value

    monkeypatch.setattr(cli.questdb, "connect", lambda conf: nullcontext(object()))
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
    valkey = Valkey()

    written = cli.collect_market_data(
        Settings(),
        [f"{index:06d}" for index in range(20)],
        MarketClient(),
        valkey,
        datetime(2026, 10, 15, 9, 0, tzinfo=KST),
        False,
    )

    assert written == 20
    assert len(events) == 20
    assert valkey.values == {"index:KOSPI": "old", "ranking:{popular}": "old"}
    assert sum(record.levelname == "ERROR" for record in caplog.records) == 3
