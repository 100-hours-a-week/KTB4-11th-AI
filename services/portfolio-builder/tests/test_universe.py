import json
from io import BytesIO
from unittest.mock import patch

import numpy as np
import pytest
import sqlalchemy as sa
from portfolio_builder.market import HttpMarket


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def universe_engine():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE corporation_indices (stock_code TEXT, index_name TEXT)"))
    yield engine
    engine.dispose()


def test_empty_universe_makes_no_requests(universe_engine):
    with patch("portfolio_builder.market.urlopen", side_effect=AssertionError("requested")):
        assert HttpMarket("http://market", universe_engine).universe_closes() == {}


def test_universe_reads_daily_bars_per_kospi200_member(universe_engine):
    with universe_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO corporation_indices VALUES (:code, 'KOSPI200')"),
            [{"code": "005930"}, {"code": "000660"}],
        )
        conn.execute(sa.text("INSERT INTO corporation_indices VALUES ('removed', 'KRX300')"))

    def respond(request, timeout):
        if "000660" in request.full_url:
            return Response(json.dumps({"items": [], "next_cursor": None}).encode())
        rows = [
            {"timestamp": "2026-09-28T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 2.0,
             "volume": 1},
            {"timestamp": "2026-09-27T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1.0,
             "volume": 1},
        ]  # fmt: skip
        return Response(json.dumps({"items": rows, "next_cursor": None}).encode())

    with patch("portfolio_builder.market.urlopen", side_effect=respond) as call:
        universe = HttpMarket("http://market", universe_engine).universe_closes()

    urls = [c.args[0].full_url for c in call.call_args_list]
    assert urls == [
        "http://market/bars/000660?timeframe=1d&limit=300",
        "http://market/bars/005930?timeframe=1d&limit=300",
    ]
    assert list(universe) == ["005930"]
    np.testing.assert_array_equal(universe["005930"], [1.0, 2.0])
