from datetime import UTC, date, datetime

import pytest
from market_collector.corporations import collect_corporations, stock_values
from market_collector.store import Store


def test_stock_values_keep_only_requested_integer_fields():
    values = stock_values(
        {
            "base_pric": "+80,000",
            "upl_pric": "+104000",
            "lst_pric": "+56000",
            "eps": "0",
            "bps": "",
            "per": "12.3",
            "cap": "1000",
        }
    )
    assert values == {
        "base_price": 80000,
        "upper_limit_price": 104000,
        "lower_limit_price": 56000,
        "eps": 0,
        "bps": None,
    }


@pytest.mark.parametrize("field", ["base_pric", "upl_pric", "lst_pric"])
def test_required_price_rejects_zero(field):
    raw = {name: "1" for name in ("base_pric", "upl_pric", "lst_pric", "eps", "bps")}
    raw[field] = "0"
    with pytest.raises(ValueError, match=field):
        stock_values(raw)


def test_store_uses_trade_day_timestamp_as_dedup_key():
    calls = []

    class Sender:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def row(self, *args, **kwargs):
            calls.append((args, kwargs))

        def flush(self):
            pass

    class DB:
        def sender(self):
            return Sender()

    store = Store(DB())
    day = date(2026, 10, 19)
    values = {
        "base_price": 100,
        "upper_limit_price": 130,
        "lower_limit_price": 70,
        "eps": None,
        "bps": 10,
        "stock_name": "삼성전자",
        "sector_name": "전기전자",
    }
    store.write_corporation("005930", day, datetime(2026, 10, 18, 23, 55, tzinfo=UTC), values)
    store.write_corporation("005930", day, datetime(2026, 10, 18, 23, 56, tzinfo=UTC), values)
    assert calls[0][1]["at"] == calls[1][1]["at"] == datetime(2026, 10, 19, tzinfo=UTC)
    assert calls[0][1]["symbols"] == {"symbol": "005930"}
    assert calls[0][1]["columns"]["stock_name"] == "삼성전자"
    assert calls[0][1]["columns"]["sector_name"] == "전기전자"
    assert "eps" not in calls[0][1]["columns"]


def test_migration_has_wal_dedup_and_required_columns():
    from pathlib import Path

    migration = (
        Path(__file__).parents[3] / "infrastructure/questdb/migrations/0003_corporations.sql"
    ).read_text()
    assert "DEDUP UPSERT KEYS(timestamp, symbol)" in migration
    assert "PARTITION BY MONTH WAL" in migration
    for column in (
        "base_price LONG",
        "upper_limit_price LONG",
        "lower_limit_price LONG",
        "eps LONG",
        "bps LONG",
    ):
        assert column in migration

    metadata_migration = (
        Path(__file__).parents[3]
        / "infrastructure/questdb/migrations/0004_corporation_metadata.sql"
    ).read_text()
    assert "ADD COLUMN stock_name VARCHAR" in metadata_migration
    assert "ADD COLUMN sector_name VARCHAR" in metadata_migration


def test_collector_joins_stock_list_metadata_with_daily_snapshot(monkeypatch):
    from contextlib import nullcontext

    from market_collector import corporations

    writes = []
    cache = []

    class Store:
        def write_corporation(self, *args):
            writes.append(args)

    class Client:
        def stock_metadata(self):
            return {"005930": ("삼성전자", "전기전자")}

        def stock_info(self, symbol):
            return {
                "base_pric": "80000",
                "upl_pric": "104000",
                "lst_pric": "56000",
                "eps": "0",
                "bps": "0",
            }

    monkeypatch.setattr(corporations.questdb, "connect", lambda conf: nullcontext(object()))
    monkeypatch.setattr(corporations, "Store", lambda db: Store())
    settings = type("Settings", (), {"questdb_conf": "unused"})()
    valkey = type("Valkey", (), {"hset": lambda self, key, mapping: cache.append((key, mapping))})()

    collect_corporations(settings, ["005930"], Client(), valkey, datetime(2026, 10, 19, tzinfo=UTC))

    assert writes[0][3]["stock_name"] == "삼성전자"
    assert writes[0][3]["sector_name"] == "전기전자"
    assert cache == [
        ("stock:metadata", {"005930": '{"stock_name": "삼성전자", "sector_name": "전기전자"}'})
    ]


def test_metadata_failure_keeps_price_limit_snapshots(monkeypatch):
    from contextlib import nullcontext

    from market_collector import corporations

    writes = []

    class Store:
        def write_corporation(self, *args):
            writes.append(args)

    class Client:
        def stock_metadata(self):
            raise RuntimeError("stock list unavailable")

        def stock_info(self, symbol):
            return {
                "base_pric": "80000",
                "upl_pric": "104000",
                "lst_pric": "56000",
                "eps": "0",
                "bps": "0",
            }

    monkeypatch.setattr(corporations.questdb, "connect", lambda conf: nullcontext(object()))
    monkeypatch.setattr(corporations, "Store", lambda db: Store())
    settings = type("Settings", (), {"questdb_conf": "unused"})()
    valkey = type("Valkey", (), {"hset": lambda self, key, mapping: None})()

    collect_corporations(settings, ["005930"], Client(), valkey, datetime(2026, 10, 19, tzinfo=UTC))

    assert writes[0][3]["base_price"] == 80000
    assert "stock_name" not in writes[0][3]
