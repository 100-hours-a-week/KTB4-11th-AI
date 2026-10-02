import numpy as np
import pytest
import sqlalchemy as sa
from portfolio_builder.market import QuestDBMarket


@pytest.fixture
def universe_engine():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE corporation_indices (stock_code TEXT, index_name TEXT)"))
    yield engine
    engine.dispose()


def test_empty_universe_does_not_open_questdb(monkeypatch, universe_engine):
    def unexpected_query(*args):
        pytest.fail("an empty universe must not query QuestDB")

    market = QuestDBMarket("unused", universe_engine)
    monkeypatch.setattr(market, "_records", unexpected_query)
    assert market.universe_closes() == {}


def test_universe_members_are_bound_and_refreshed_for_each_read(monkeypatch, universe_engine):
    special_code = "member'code"
    with universe_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO corporation_indices VALUES (:code, 'KOSPI200')"),
            [{"code": "005930"}, {"code": special_code}],
        )
        conn.execute(sa.text("INSERT INTO corporation_indices VALUES ('removed', 'KRX300')"))
    queries = []

    def records(sql, binds=None):
        queries.append((sql, binds))
        return [{"symbol": binds[0], "close": 10.0}]

    market = QuestDBMarket("unused", universe_engine)
    monkeypatch.setattr(market, "_records", records)
    universe = market.universe_closes()
    assert list(universe) == ["005930"]
    np.testing.assert_array_equal(universe["005930"], [10.0])
    sql, binds = queries[0]
    assert special_code not in sql
    assert binds == ["005930", special_code]
    assert "cast(symbol AS VARCHAR) IN ($1, $2)" in sql
    with universe_engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM corporation_indices WHERE stock_code = '005930'"))
        conn.execute(sa.text("INSERT INTO corporation_indices VALUES ('new', 'KOSPI200')"))
    market.universe_closes()
    assert queries[1][1] == [special_code, "new"]
