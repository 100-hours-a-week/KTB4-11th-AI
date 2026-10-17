import asyncio
import json
from datetime import datetime

from fastapi.testclient import TestClient
from ktb_core.krx import KST
from market_http.app import create_app
from market_http.repositories.rankings import read_rankings

from .example_entities import INDEX, RANKING


class Valkey:
    def __init__(self, sets):
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


def test_http_serves_indices_and_popular_rankings():
    index_payload = INDEX.model_dump(mode="json")
    index_payload["ts"] = index_payload.pop("timestamp")
    assert RANKING.updated_at is not None
    valkey = Valkey(
        {
            "index:KOSPI": json.dumps(index_payload),
            "ranking:popular": {RANKING.items[0].code: RANKING.items[0].score, "000660": 1},
            "ranking:popular:updated_at": RANKING.updated_at.isoformat(),
        }
    )
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
