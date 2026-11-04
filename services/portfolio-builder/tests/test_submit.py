import logging
import threading

import pytest
import sqlalchemy as sa
from portfolio_builder.briefing.repository import find_previous_portfolio
from portfolio_builder.competition.repository import store_settings
from portfolio_builder.errors import PortfolioRejected, ToolError
from portfolio_builder.portfolio import excluded_company_ids
from portfolio_builder.tools.submit import submit_tool

SAMSUNG = "00126380"
HYNIX = "00164779"
LGES = "01515323"
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


def test_excluded_company_ids_resolves_corp_codes_through_stock_code(engine):
    assert excluded_company_ids(engine, ["T1"]) == frozenset({SAMSUNG, HYNIX})


def test_no_excluded_themes_resolves_to_an_empty_set(engine):
    assert excluded_company_ids(engine, []) == frozenset()


def test_an_unknown_theme_code_resolves_to_an_empty_set(engine):
    assert excluded_company_ids(engine, ["T404"]) == frozenset()


def test_a_holding_in_an_excluded_theme_is_rejected(engine, news_client):
    tool = submit_tool(
        engine, frozenset(), "m", news_client, Recorder(), excluded=frozenset({SAMSUNG})
    )

    with pytest.raises(PortfolioRejected) as rejected:
        tool.invoke(_call(VALID))

    assert f"holdings: {SAMSUNG} is in an excluded theme" in rejected.value.errors
    assert _rows(engine) == []


def test_a_company_outside_every_excluded_theme_is_accepted(engine, news_client):
    tool = submit_tool(
        engine, frozenset(), "m", news_client, Recorder(), excluded=frozenset({SAMSUNG})
    )
    holding = {"company_id": LGES, "weight": 3, "reason": "r", "cited_cluster_ids": [1]}

    tool.invoke(_call({**VALID, "holdings": [holding]}))

    assert len(_rows(engine)) == 1


def test_an_excluded_previous_holding_can_be_exited(engine, news_client):
    tool = submit_tool(
        engine,
        frozenset({SAMSUNG}),
        "m",
        news_client,
        Recorder(),
        excluded=frozenset({SAMSUNG}),
    )
    submission = {
        **VALID,
        "holdings": [{"company_id": LGES, "weight": 3, "reason": "r", "cited_cluster_ids": [1]}],
        "exits": [{"company_id": SAMSUNG, "reason": "제외 테마", "cited_cluster_ids": [1]}],
    }

    tool.invoke(_call(submission))

    assert len(_rows(engine)) == 1


def test_exclusion_errors_join_the_other_errors_in_one_rejection(engine, news_client):
    tool = submit_tool(
        engine, frozenset(), "m", news_client, Recorder(), excluded=frozenset({SAMSUNG})
    )

    with pytest.raises(PortfolioRejected) as rejected:
        tool.invoke(_call({**VALID, "commentary": ""}))

    assert len(rejected.value.errors) == 2


def test_a_company_in_two_themes_is_excluded_when_either_one_is_excluded(engine, news_client):
    assert excluded_company_ids(engine, ["T2"]) == frozenset({SAMSUNG})

    tool = submit_tool(
        engine, frozenset(), "m", news_client, Recorder(), excluded=frozenset({SAMSUNG})
    )

    with pytest.raises(PortfolioRejected) as rejected:
        tool.invoke(_call(VALID))

    assert f"holdings: {SAMSUNG} is in an excluded theme" in rejected.value.errors
    assert _rows(engine) == []


def test_a_theme_code_no_longer_in_themes_excludes_nothing(engine):
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM theme_corporations WHERE theme_code = 'T1'"))
        conn.execute(sa.text("DELETE FROM themes WHERE theme_code = 'T1'"))

    assert excluded_company_ids(engine, ["T1"]) == frozenset()


def test_declared_constraints_are_stored_on_the_portfolio(engine, news_client):
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder())

    tool.invoke(_call({**VALID, "constraints": {"max_holdings": 5}}))

    with engine.connect() as conn:
        stored = conn.execute(
            sa.text("SELECT acknowledged_constraints FROM portfolios")
        ).scalar_one()
    assert stored == {"max_holdings": 5, "max_weight_per_holding": None, "min_cash_weight": None}


def test_no_declared_constraints_store_null(engine, news_client):
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder())

    tool.invoke(_call(VALID))

    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT acknowledged_constraints FROM portfolios")).scalar_one()
            is None
        )


def test_excluding_every_company_leaves_a_cash_only_portfolio_submittable(engine, news_client):
    every = excluded_company_ids(engine, ["T1"]) | {LGES}
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder(), excluded=every)

    tool.invoke(_call({**VALID, "holdings": [], "cash_weight": 1}))

    [(_, cash_weight)] = _rows(engine)
    assert cash_weight == pytest.approx(1.0)


def test_a_submission_with_a_participant_id_links_the_portfolio_in_the_same_call(
    engine, news_client, _request
):
    settings, _ = store_settings(engine, _request())
    tool = submit_tool(
        engine, frozenset(), "m", news_client, Recorder(), participant_id=settings.participant_id
    )

    tool.invoke(_call(VALID))

    [(portfolio_id, _)] = _rows(engine)
    with engine.connect() as conn:
        linked = conn.execute(
            sa.text(
                "SELECT 1 FROM competition_portfolios"
                " WHERE participant_id = :p AND portfolio_id = :pf"
            ),
            {"p": settings.participant_id, "pf": portfolio_id},
        ).first()
        previous = find_previous_portfolio(conn)
    assert linked is not None
    assert previous is None


def test_a_submission_with_no_participant_id_inserts_no_link_row(engine, news_client):
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder())

    tool.invoke(_call(VALID))

    [(portfolio_id, _)] = _rows(engine)
    with engine.connect() as conn:
        link_count = conn.execute(
            sa.text("SELECT count(*) FROM competition_portfolios")
        ).scalar_one()
        previous = find_previous_portfolio(conn)
    assert link_count == 0
    assert previous is not None
    assert previous.portfolio["id"] == portfolio_id
