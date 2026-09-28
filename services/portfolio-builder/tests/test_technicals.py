import json
from datetime import UTC, datetime

import numpy as np
import pytest
from portfolio_builder import market as market_module
from portfolio_builder.errors import NoMarketData, UnknownCompany
from portfolio_builder.evidence import Bars
from portfolio_builder.market import QuestDBMarket
from portfolio_builder.tools.technicals import technicals_tool

AS_OF = datetime(2026, 9, 28, 6, 30, tzinfo=UTC)


def _bars(n: int) -> Bars:
    close = np.arange(1, n + 1, dtype=np.float64)
    return Bars(close.copy(), close.copy(), close, np.full(n, 1000.0))


class FakeMarket:
    def __init__(self, n: int = 300):
        self.n = n
        self.calls: list[tuple[str, str]] = []
        self.universe_calls = 0

    def bars(self, symbol, timeframe):
        self.calls.append((symbol, timeframe))
        if self.n == 0:
            return _bars(0), None
        return _bars(self.n), AS_OF

    def universe_closes(self):
        self.universe_calls += 1
        return {"005930": _bars(300).close, "000660": _bars(300).close * 2}


def test_daily_evidence_for_a_company_found_by_name(engine):
    market = FakeMarket()
    tool = technicals_tool(engine, market)

    result = json.loads(tool.invoke({"name": "㈜삼성전자", "timeframe": "1d"}))

    assert market.calls == [("005930", "1d")]
    assert result["company_id"] == "00126380"
    assert result["stock_code"] == "005930"
    assert result["timeframe"] == "1d"
    assert result["bars"] == 300
    assert result["as_of"] == AS_OF.isoformat()
    assert "return_5d" in result["evidence"]
    assert result["unavailable"]["market_excess_return_5d"] == "benchmark data not collected"


def test_intraday_evidence_skips_the_universe(engine):
    market = FakeMarket()
    tool = technicals_tool(engine, market)

    result = json.loads(tool.invoke({"name": "00164779", "timeframe": "15m"}))

    assert market.calls == [("000660", "15m")]
    assert market.universe_calls == 0
    assert "return_5" in result["evidence"]
    assert "momentum_12m_skip1m" not in result["evidence"]


def test_the_universe_is_read_once_per_run(engine):
    market = FakeMarket()
    tool = technicals_tool(engine, market)

    tool.invoke({"name": "삼성전자", "timeframe": "1d"})
    tool.invoke({"name": "SK하이닉스", "timeframe": "1d"})

    assert market.universe_calls == 1


def test_timeframe_is_required(engine):
    with pytest.raises(Exception, match="timeframe"):
        technicals_tool(engine, FakeMarket()).invoke({"name": "삼성전자"})


def test_unknown_company_lists_candidates(engine):
    with pytest.raises(UnknownCompany, match="삼성전자"):
        technicals_tool(engine, FakeMarket()).invoke({"name": "삼성", "timeframe": "1d"})


def test_no_bars_is_a_recoverable_error(engine):
    with pytest.raises(NoMarketData, match="373220"):
        technicals_tool(engine, FakeMarket(n=0)).invoke(
            {"name": "LG에너지솔루션", "timeframe": "1d"}
        )


class FakeResult:
    def __init__(self, records):
        self.records = records

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def to_pandas(self):
        records = self.records

        class Frame:
            def to_dict(self, orient):
                assert orient == "records"
                return records

        return Frame()


class FakeDB:
    def __init__(self, answers):
        self.answers = answers
        self.queries: list[tuple[str, list]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def query(self, sql, binds=None):
        self.queries.append((sql, binds))
        return FakeResult(self.answers.pop(0))


@pytest.mark.parametrize(
    ("timeframe", "view", "session"),
    [("1m", "bars_1m", True), ("15m", "bars_15m", False), ("1h", "bars_1h", False),
     ("1d", "bars_1d", True)],
)  # fmt: skip
def test_bars_reads_the_timeframe_view_newest_first_and_reverses(
    monkeypatch, timeframe, view, session
):
    rows = [
        {"ts": AS_OF, "high": 3.0, "low": 1.0, "close": 2.0, "volume": 30},
        {"ts": datetime(2026, 9, 27, tzinfo=UTC), "high": 2.0, "low": 1.0, "close": 1.5,
         "volume": 20},
    ]  # fmt: skip
    db = FakeDB([rows])
    monkeypatch.setattr(market_module.questdb, "connect", lambda conf: db)

    bars, as_of = QuestDBMarket("http::addr=x:9000;").bars("005930", timeframe)

    sql, binds = db.queries[0]
    assert f"FROM {view} " in sql
    assert ("session = 'regular'" in sql) is session
    assert binds == ["005930", 300]
    assert bars.close.tolist() == [1.5, 2.0]
    assert bars.volume.dtype == np.float64
    assert as_of == AS_OF


def test_bars_rejects_an_unknown_timeframe():
    with pytest.raises(KeyError):
        QuestDBMarket("x").bars("005930", "5m")


def test_universe_closes_groups_the_latest_snapshot_by_symbol(monkeypatch):
    members = [{"symbol": "005930"}, {"symbol": "000660"}]
    closes = [
        {"symbol": "000660", "close": 10.0},
        {"symbol": "005930", "close": 1.0},
        {"symbol": "005930", "close": 2.0},
        {"symbol": "999999", "close": 5.0},
    ]
    db = FakeDB([members, closes])
    monkeypatch.setattr(market_module.questdb, "connect", lambda conf: db)

    universe = QuestDBMarket("x").universe_closes()

    assert {k: v.tolist() for k, v in universe.items()} == {
        "005930": [1.0, 2.0],
        "000660": [10.0],
    }
