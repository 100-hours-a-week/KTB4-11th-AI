import logging

import pytest
import sqlalchemy as sa
from competition.repository import store_settings
from portfolio_builder.competition.portfolio import excluded_company_ids
from portfolio_builder.competition.submit import submit_tool as _submit_tool
from portfolio_builder.errors import PortfolioRejected
from portfolio_builder.scheduled.repository import find_previous_portfolio

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


@pytest.fixture(autouse=True)
def participant(engine, _request):
    store_settings(engine, _request())


def submit_tool(*args, **kwargs):
    return _submit_tool(*args, **{"participant_id": 1, "excluded": frozenset(), **kwargs})


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


def test_declared_constraints_are_stored_on_the_portfolio(engine, news_client):
    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder())

    tool.invoke(_call({**VALID, "constraints": {"max_holdings": 5}}))

    with engine.connect() as conn:
        stored = conn.execute(
            sa.text("SELECT acknowledged_constraints FROM portfolios")
        ).scalar_one()
    assert stored == {"max_holdings": 5, "max_weight_per_holding": None, "min_cash_weight": None}
