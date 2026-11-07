from contextlib import nullcontext

from market_http.repositories import corporations


def test_limits_query_uses_latest_valid_snapshot_and_binds_symbols(monkeypatch):
    queries = []

    class Result:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def to_pandas(self):
            import pandas as pd

            return pd.DataFrame(
                [{"symbol": "005930", "upper_limit_price": 104000, "lower_limit_price": 56000}]
            )

    class DB:
        def query(self, sql, binds):
            queries.append((sql, binds))
            return Result()

    monkeypatch.setattr(corporations.questdb, "connect", lambda conf: nullcontext(DB()))

    assert corporations.latest_price_limits("conf", ["005930"]) == {
        "005930": {"upper_limit_price": 104000, "lower_limit_price": 56000}
    }
    assert "upper_limit_price IS NOT NULL AND lower_limit_price IS NOT NULL" in queries[0][0]
    assert "LATEST ON timestamp PARTITION BY symbol" in queries[0][0]
    assert queries[0][1] == ["005930"]
