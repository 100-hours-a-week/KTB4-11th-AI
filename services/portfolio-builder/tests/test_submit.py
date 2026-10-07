import logging
import threading

import pytest
import sqlalchemy as sa
from portfolio_builder.errors import PortfolioRejected, ToolError
from portfolio_builder.tools.submit import submit_tool

SAMSUNG = "00126380"
VALID = {
    "holdings": [
        {"company_id": SAMSUNG, "weight": 3, "reason": "HBM 공급 확대", "cited_cluster_ids": [1]}
    ],
    "exits": [],
    "cash_weight": 1,
    "commentary": "HBM 수요에 집중",
}


def _call(args, call_id="c1"):
    return {"name": "submit_portfolio", "args": args, "id": call_id, "type": "tool_call"}


class Recorder:
    def __init__(self):
        self.events = []

    def info(self, event, **fields):
        self.events.append((event, logging.INFO, fields))

    def warning(self, event, **fields):
        self.events.append((event, logging.WARNING, fields))

    def error(self, event, **fields):
        self.events.append((event, logging.ERROR, fields))


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT id, cash_weight FROM portfolios")).all()


def test_saves_a_normalised_portfolio_and_returns_its_id_in_state(engine, news_client):
    tool = submit_tool(engine, frozenset(), "openrouter/m", news_client, Recorder())

    command = tool.invoke(_call(VALID))

    [(portfolio_id, cash_weight)] = _rows(engine)
    assert command.update["portfolio_id"] == portfolio_id
    assert command.update["messages"][0].content == f"Saved portfolio {portfolio_id}."
    assert command.update["messages"][0].tool_call_id == "c1"
    assert cash_weight == pytest.approx(0.25)


def test_rejection_lists_every_error_and_logs_it(engine, news_client):
    log = Recorder()
    tool = submit_tool(engine, frozenset(), "m", news_client, log)
    bad = {**VALID, "holdings": [{**VALID["holdings"][0], "reason": ""}], "commentary": ""}

    with pytest.raises(PortfolioRejected) as rejected:
        tool.invoke(_call(bad))

    assert rejected.value.errors == [
        f"holdings: {SAMSUNG} is entering the portfolio and needs a reason",
        "commentary must not be empty",
    ]
    assert log.events == [("validation_failed", logging.WARNING, {"errors": rejected.value.errors})]
    assert _rows(engine) == []


def test_a_save_failure_is_returned_as_a_rejection(engine, news_client):
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder())
    bad = {**VALID, "holdings": [{**VALID["holdings"][0], "cited_cluster_ids": [404]}]}

    with pytest.raises(PortfolioRejected, match="cited_cluster_ids not found: 404"):
        tool.invoke(_call(bad))


def test_a_second_submit_is_refused(engine, news_client):
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder())
    tool.invoke(_call(VALID))

    with pytest.raises(ToolError, match="already saved"):
        tool.invoke(_call(VALID, "c2"))
    assert len(_rows(engine)) == 1


def test_concurrent_submits_write_one_portfolio(engine, news_client):
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder())
    outcomes = []

    def submit(call_id):
        try:
            tool.invoke(_call(VALID, call_id))
            outcomes.append("saved")
        except ToolError:
            outcomes.append("refused")

    threads = [threading.Thread(target=submit, args=(f"c{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["refused", "refused", "refused", "saved"]
    assert len(_rows(engine)) == 1
