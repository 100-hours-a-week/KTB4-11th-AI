import asyncio
import json
from contextlib import nullcontext
from datetime import datetime

import sqlalchemy as sa
from fastapi.testclient import TestClient
from ktb_core.krx import KST
from market_http.app import create_app
from market_http.repositories import rankings as ranking_repository
from market_http.repositories.rankings import read_rankings
from market_http.services import rankings as ranking_service

from .example_entities import INDEX, RANKING
from .test_market_subscription import FakeValkey


class Valkey(FakeValkey):
    def __init__(self, sets):
        super().__init__()
        self.sets = sets

    async def exists(self, key):
        return int(key in self.sets)

    async def zrevrange(self, key, start, stop, withscores=False):
        rows = sorted(self.sets.get(key, {}).items(), key=lambda item: item[1], reverse=True)
        return rows[start : stop + 1]

    async def zrevrangebyscore(self, key, maximum, minimum, start, num, withscores=False):
        rows = sorted(
            ((code, score) for code, score in self.sets.get(key, {}).items() if score > 0),
            key=lambda item: item[1],
            reverse=True,
        )
        return rows[start : start + num]

    async def zrangebyscore(self, key, minimum, maximum, start, num, withscores=False):
        rows = sorted(
            ((code, score) for code, score in self.sets.get(key, {}).items() if score < 0),
            key=lambda item: item[1],
        )
        return rows[start : start + num]

    async def get(self, key):
        return self.sets.get(key)

    def pipeline(self, transaction):
        assert transaction is False

        class Pipeline:
            def __init__(self, sets):
                self.sets = sets
                self.keys = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def get(self, key):
                self.keys.append(key)

            async def execute(self):
                return [self.sets.get(key) for key in self.keys]

        return Pipeline(self.sets)


def test_rankings_switch_at_0910_and_keep_signed_change_rate():
    valkey = Valkey(
        {
            "ranking:change_rate:20261014": {"005930": -3.0},
            "ranking:change_rate:20261015": {"005930": 2.0, "000660": -1.0},
        }
    )

    before = asyncio.run(
        read_rankings(valkey, "losers", 100, datetime(2026, 10, 15, 9, 9, tzinfo=KST))
    )
    after = asyncio.run(
        read_rankings(valkey, "losers", 100, datetime(2026, 10, 15, 9, 10, tzinfo=KST))
    )

    assert before == {
        "as_of": "2026-10-14",
        "updated_at": None,
        "items": [{"rank": 1, "code": "005930", "score": -3.0}],
    }
    assert after == {
        "as_of": "2026-10-15",
        "updated_at": None,
        "items": [{"rank": 1, "code": "000660", "score": -1.0}],
    }


def test_rankings_fall_back_when_current_day_is_missing():
    valkey = Valkey({"ranking:volume:20261014": {"005930": 100}})

    result = asyncio.run(read_rankings(valkey, "volume", 5, datetime(2026, 10, 15, 10, tzinfo=KST)))

    assert result["as_of"] == "2026-10-14"
    assert result["items"] == [{"rank": 1, "code": "005930", "score": 100.0}]


def test_popular_rankings_read_hash_tagged_keys():
    valkey = Valkey(
        {
            "ranking:{popular}": {"005930": 100},
            "ranking:{popular}:updated_at": "2026-10-15T09:01:00+09:00",
        }
    )

    result = asyncio.run(
        read_rankings(valkey, "popular", 5, datetime(2026, 10, 15, 10, tzinfo=KST))
    )

    assert result == {
        "as_of": "2026-10-15",
        "updated_at": "2026-10-15T09:01:00+09:00",
        "items": [{"rank": 1, "code": "005930", "score": 100}],
    }


def test_http_serves_indices_and_popular_rankings(monkeypatch):
    index_payload = INDEX.model_dump(mode="json")
    index_payload["ts"] = index_payload.pop("timestamp")
    assert RANKING.updated_at is not None
    valkey = Valkey(
        {
            "index:KOSPI": json.dumps(index_payload),
            "ranking:{popular}": {RANKING.items[0].code: RANKING.items[0].score, "000660": 1},
            "ranking:{popular}:updated_at": RANKING.updated_at.isoformat(),
            "price:005930": json.dumps({"code": "005930", "price": 82100, "rate": -0.48}),
        }
    )
    monkeypatch.setattr(
        ranking_service,
        "corporation_metadata",
        lambda conf, codes: {
            "005930": ranking_repository.CorporationMetadata("삼성전자", "전기전자")
        },
    )
    monkeypatch.setattr(ranking_service, "questdb_ranking_details", lambda conf, codes: {})

    with TestClient(create_app(valkey, "unused")) as client:
        index = client.get("/indices/KOSPI")
        popular = client.get("/rankings/popular?limit=1")
        invalid = client.get("/rankings/popular?limit=101")

    assert index.status_code == 200
    assert index.json()["value"] == 2750.12
    assert index.json()["timestamp"] == "2026-10-15T09:01:00+09:00"
    assert "ts" not in index.json()
    assert popular.status_code == 200
    assert popular.json() == RANKING.model_dump(mode="json")
    assert invalid.status_code == 422


def test_http_keeps_missing_details_null_and_ranking_order(monkeypatch):
    monkeypatch.setattr(ranking_service, "questdb_ranking_details", lambda conf, codes: {})
    monkeypatch.setattr(ranking_service, "corporation_metadata", lambda engine, codes: {})
    valkey = Valkey({"ranking:{popular}": {"005930": 100, "000660": 50}})

    with TestClient(create_app(valkey, "unused")) as client:
        response = client.get("/rankings/popular")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "rank": 1,
            "code": "005930",
            "score": 100.0,
            "stock_name": None,
            "sector_name": None,
            "price": None,
            "change_rate": None,
        },
        {
            "rank": 2,
            "code": "000660",
            "score": 50.0,
            "stock_name": None,
            "sector_name": None,
            "price": None,
            "change_rate": None,
        },
    ]


def test_http_returns_100_ordered_popular_items(monkeypatch):
    popular_codes = [f"{index:06d}" for index in range(1, 101)]
    updated_at = "2026-10-22T10:01:00+09:00"
    valkey = Valkey(
        {
            "ranking:{popular}": {code: 100 - index for index, code in enumerate(popular_codes)},
            "ranking:{popular}:updated_at": updated_at,
        }
    )
    monkeypatch.setattr(ranking_service, "questdb_ranking_details", lambda conf, codes: {})
    monkeypatch.setattr(ranking_service, "corporation_metadata", lambda engine, codes: {})

    with TestClient(create_app(valkey, "unused")) as client:
        response = client.get("/rankings/popular?limit=100")

    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of"] == "2026-10-22"
    assert payload["updated_at"] == updated_at
    assert len(payload["items"]) == 100
    assert [item["code"] for item in payload["items"]] == popular_codes
    assert [item["rank"] for item in payload["items"]] == list(range(1, 101))
    assert [item["score"] for item in payload["items"]] == list(range(100, 0, -1))


def test_corporation_metadata_reads_matching_codes_from_postgres():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE corporations "
                "(stock_code TEXT PRIMARY KEY, name TEXT, sector_name TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO corporations VALUES ('005930', '삼성전자', '전기전자'),"
                " ('000660', 'SK하이닉스', '')"
            )
        )

    assert ranking_repository.corporation_metadata(engine, ["005930", "000660", "999999"]) == {
        "005930": ranking_repository.CorporationMetadata("삼성전자", "전기전자"),
        "000660": ranking_repository.CorporationMetadata("SK하이닉스", ""),
    }
    engine.dispose()


def test_questdb_details_batch_latest_price_and_matching_day_base(monkeypatch):
    queries = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def to_pandas(self):
            import pandas as pd

            return pd.DataFrame(self.rows)

    class DB:
        def query(self, sql, binds):
            queries.append((sql, binds))
            if "FROM corporations" in sql:
                return Result(
                    [
                        {
                            "symbol": "005930",
                            "stock_name": "삼성전자",
                            "sector_name": "전기전자",
                            "base_price": 82500,
                            "trade_date": "2026-10-15",
                        }
                    ]
                )
            return Result(
                [
                    {
                        "symbol": "005930",
                        "timestamp": datetime.fromisoformat("2026-10-15T00:01:00+00:00"),
                        "close": 82100,
                    }
                ]
            )

    monkeypatch.setattr(ranking_repository.questdb, "connect", lambda conf: nullcontext(DB()))

    assert ranking_repository.questdb_ranking_details("unused", ["005930", "000660"]) == {
        "005930": ranking_repository.RankingPriceDetails(82100, -0.48)
    }
    assert len(queries) == 2
    assert all(binds == ["005930", "000660"] for _, binds in queries)
    assert all("LATEST ON timestamp PARTITION BY symbol" in sql for sql, _ in queries)


def test_questdb_details_use_today_base(monkeypatch):
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def to_pandas(self):
            import pandas as pd

            return pd.DataFrame(self.rows)

    class DB:
        def query(self, sql, binds):
            if "FROM corporations" in sql:
                return Result(
                    [{"symbol": "005930", "base_price": 82500, "trade_date": "2026-10-15"}]
                )
            return Result(
                [
                    {
                        "symbol": "005930",
                        "timestamp": datetime.fromisoformat("2026-10-15T00:01:00+00:00"),
                        "close": 82100,
                    }
                ]
            )

    monkeypatch.setattr(ranking_repository.questdb, "connect", lambda conf: nullcontext(DB()))

    assert ranking_repository.questdb_ranking_details("unused", ["005930"]) == {
        "005930": ranking_repository.RankingPriceDetails(82100, -0.48)
    }
