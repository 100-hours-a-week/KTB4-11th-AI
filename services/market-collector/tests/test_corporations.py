from datetime import UTC, date, datetime

import pytest
from market_collector import corporations
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


def test_stock_values_normalizes_signed_prices_but_preserves_negative_eps():
    assert stock_values(
        {"base_pric": "12000", "upl_pric": "+15000", "lst_pric": "-11000", "eps": "-23", "bps": "4"}
    ) == {
        "base_price": 12000,
        "upper_limit_price": 15000,
        "lower_limit_price": 11000,
        "eps": -23,
        "bps": 4,
    }


def test_stock_values_rejects_malformed_price_with_original_value():
    with pytest.raises(ValueError, match="not-a-price"):
        stock_values(
            {
                "base_pric": "12000",
                "upl_pric": "+15000",
                "lst_pric": "not-a-price",
                "eps": "-23",
                "bps": "4",
            }
        )


def test_collect_corporations_writes_snapshot_with_signed_price(monkeypatch):
    written = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Client:
        def stock_info(self, symbol):
            return {
                "base_pric": "12000",
                "upl_pric": "+15000",
                "lst_pric": "-11000",
                "eps": "-23",
                "bps": "4",
            }

    class SnapshotStore:
        def __init__(self, db):
            pass

        def write_corporation(self, symbol, day, fetched_at, values):
            written.append((symbol, day, values))

    monkeypatch.setattr(corporations.questdb, "connect", lambda conf: Connection())
    monkeypatch.setattr(corporations, "Store", SnapshotStore)
    settings = type("Settings", (), {"questdb_conf": "test"})()
    collect_corporations(settings, ["000080"], Client(), datetime(2026, 10, 19, tzinfo=UTC))
    assert written == [
        (
            "000080",
            date(2026, 10, 19),
            {
                "base_price": 12000,
                "upper_limit_price": 15000,
                "lower_limit_price": 11000,
                "eps": -23,
                "bps": 4,
            },
        )
    ]


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
    }
    store.write_corporation("005930", day, datetime(2026, 10, 18, 23, 55, tzinfo=UTC), values)
    store.write_corporation("005930", day, datetime(2026, 10, 18, 23, 56, tzinfo=UTC), values)
    assert calls[0][1]["at"] == calls[1][1]["at"] == datetime(2026, 10, 19, tzinfo=UTC)
    assert calls[0][1]["symbols"] == {"symbol": "005930"}
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
