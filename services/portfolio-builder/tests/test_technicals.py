import json
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import patch

import numpy as np
import pytest
from portfolio_builder.errors import NoMarketData, UnknownCompany
from portfolio_builder.market import HttpMarket
from portfolio_builder.measurement import Bars
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
    assert result["signals"]["trend"]["state"] == "established_uptrend"
    assert result["signals"]["trend"]["evidence"]["price_vs_sma20_pct"] > 0
    assert result["unavailable"]["market_excess_return_5"] == "benchmark data not collected"


def test_intraday_evidence_skips_the_universe(engine):
    market = FakeMarket()
    tool = technicals_tool(engine, market)

    result = json.loads(tool.invoke({"name": "00164779", "timeframe": "15m"}))

    assert market.calls == [("000660", "15m")]
    assert market.universe_calls == 0
    assert "short_term_move" in result["signals"]
    assert "relative_strength" not in result["signals"]
    assert result["unavailable"]["relative_strength"].endswith("measured on daily bars only")


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


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _page(rows):
    return Response(json.dumps({"items": rows, "next_cursor": None}).encode())


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1h", "1d"])
def test_http_market_requests_300_bars_and_orders_them_oldest_first(timeframe):
    newest = {
        "timestamp": "2026-09-28T06:30:00Z",
        "open": 1.0,
        "high": 3.0,
        "low": 1.0,
        "close": 2.0,
        "volume": 30,
    }
    older = {"timestamp": "2026-09-27T00:00:00Z", "open": 1.0, "high": 2.0, "low": 1.0,
             "close": 1.5, "volume": 20}  # fmt: skip
    with patch("portfolio_builder.market.urlopen", return_value=_page([newest, older])) as call:
        bars, as_of = HttpMarket("http://market", None).bars("005930", timeframe)

    request = call.call_args.args[0]
    assert request.full_url == f"http://market/bars/005930?timeframe={timeframe}&limit=300"
    assert bars.close.tolist() == [1.5, 2.0]
    assert bars.volume.dtype == np.float64
    assert as_of == AS_OF


def test_http_market_rejects_an_unknown_timeframe():
    with pytest.raises(KeyError):
        HttpMarket("http://market", None).bars("005930", "5m")


def test_http_market_returns_empty_bars_for_unknown_symbol():
    with patch("portfolio_builder.market.urlopen", return_value=_page([])):
        bars, as_of = HttpMarket("http://market", None).bars("999999", "1d")

    assert bars.close.size == 0
    assert as_of is None
