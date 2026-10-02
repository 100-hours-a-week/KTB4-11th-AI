import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event, Lock

import numpy as np
import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from portfolio_builder.company import Company
from portfolio_builder.measurement import Bars
from portfolio_builder.tools import technicals


class Market:
    def __init__(self):
        self.calls = 0
        self.close = np.arange(1, 301, dtype=np.float64)

    def bars(self, symbol, timeframe):
        return (
            Bars(self.close, self.close, self.close, np.full(300, 1000.0)),
            datetime(2026, 10, 2, tzinfo=UTC),
        )

    def universe_closes(self):
        self.calls += 1
        return {"005930": self.close}


@pytest.fixture(autouse=True)
def resolved_company(monkeypatch):
    monkeypatch.setattr(
        technicals, "resolve_company", lambda engine, name: Company("00126380", name, "005930")
    )


def test_parallel_daily_tool_calls_share_the_first_universe_query():
    class BlockingMarket(Market):
        def __init__(self):
            super().__init__()
            self.barrier = Barrier(3, timeout=5)
            self.started = Event()
            self.duplicated = Event()
            self.release = Event()
            self.lock = Lock()

        def bars(self, symbol, timeframe):
            self.barrier.wait()
            return super().bars(symbol, timeframe)

        def universe_closes(self):
            with self.lock:
                self.calls += 1
                if self.calls > 1:
                    self.duplicated.set()
            self.started.set()
            assert self.release.wait(5)
            return {"005930": self.close}

    market = BlockingMarket()
    node = ToolNode([technicals.technicals_tool(None, market)])
    graph = StateGraph(MessagesState)
    graph.add_node("tools", node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    app = graph.compile()
    message = AIMessage(
        "",
        tool_calls=[
            {
                "name": "analyze_technicals",
                "args": {"name": "삼성전자", "timeframe": "1d"},
                "id": str(i),
                "type": "tool_call",
            }
            for i in range(3)
        ],
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(app.invoke, {"messages": [message]})
        try:
            assert market.started.wait(5)
            duplicated = market.duplicated.wait(0.2)
        finally:
            market.release.set()
        messages = result.result(timeout=5)["messages"][1:]
    assert not duplicated
    assert market.calls == 1
    assert len(messages) == 3
    assert all(json.loads(message.content)["stock_code"] == "005930" for message in messages)


def test_failed_first_universe_query_can_be_retried():
    class FailingMarket(Market):
        def universe_closes(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("universe query failed")
            return {"005930": self.close}

    market = FailingMarket()
    tool = technicals.technicals_tool(None, market)
    args = {"name": "삼성전자", "timeframe": "1d"}
    with pytest.raises(RuntimeError, match="universe query failed"):
        tool.invoke(args)
    tool.invoke(args)
    tool.invoke(args)
    assert market.calls == 2


def test_a_new_run_reads_a_fresh_universe():
    market = Market()
    args = {"name": "삼성전자", "timeframe": "1d"}
    first_run = technicals.technicals_tool(None, market)
    first_run.invoke(args)
    first_run.invoke(args)
    technicals.technicals_tool(None, market).invoke(args)
    assert market.calls == 2


@pytest.mark.parametrize("timeframe", ["1m", "15m", "1h"])
def test_intraday_calls_do_not_load_the_universe(timeframe):
    market = Market()
    technicals.technicals_tool(None, market).invoke({"name": "삼성전자", "timeframe": timeframe})
    assert market.calls == 0
