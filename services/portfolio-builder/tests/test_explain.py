import json
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.backend import store_settings
from portfolio_builder.competition.repository import (
    claim_job,
    finish_job,
    generation_key,
    link_portfolio,
)
from portfolio_builder.explain import (
    ExplanationRejected,
    Explanations,
    Target,
    explain,
    load_targets,
    load_unexplained,
    save_explanations,
)
from portfolio_builder.portfolio import mark_explanation_failed
from pydantic import ValidationError

SAMSUNG = "00126380"
HYNIX = "00164779"
TARGETS = [
    Target(company_id=SAMSUNG, name="삼성전자", exiting=False, weight=0.6, previous_weight=None),
    Target(company_id=HYNIX, name="SK하이닉스", exiting=True, weight=None, previous_weight=0.3),
]
SIDE = {
    "reason": "HBM 수요로 직접 수혜를 받아요.",
    "reasonings": [{"label": "HBM", "body": "늘었어요."}],
}
VALID = {
    "stocks": [
        {"company_id": SAMSUNG, "buy": SIDE, "sell": SIDE},
        {"company_id": HYNIX, "buy": None, "sell": SIDE},
    ]
}
TRACE = [TraceEntry(turn=1, kind="tool", name="search_news_cluster", args={}, result="x" * 50)]
TRACE_ROW = [{"turn": 1, "kind": "model"}]


def _validate(raw):
    return Explanations.model_validate(raw, context={"targets": TARGETS})


def test_a_complete_explanation_validates():
    assert len(_validate(VALID).stocks) == 2


def test_every_coverage_error_is_reported_together():
    raw = {
        "stocks": [
            {"company_id": SAMSUNG, "buy": None, "sell": SIDE},
            {"company_id": HYNIX, "buy": SIDE, "sell": SIDE},
            {"company_id": "99999999", "buy": None, "sell": SIDE},
        ]
    }

    with pytest.raises(ValidationError) as error:
        _validate(raw)

    message = str(error.value)
    assert f"{SAMSUNG}: a holding needs a buy explanation" in message
    assert f"{HYNIX}: an exit has no buy explanation" in message
    assert "99999999: not in the portfolio" in message


def test_a_missing_stock_is_reported():
    with pytest.raises(ValidationError, match=f"{HYNIX}: missing"):
        _validate({"stocks": VALID["stocks"][:1]})


def test_a_duplicate_stock_is_reported():
    with pytest.raises(ValidationError, match=f"{SAMSUNG}: explained twice"):
        _validate({"stocks": [*VALID["stocks"], VALID["stocks"][0]]})


def test_an_empty_label_is_rejected():
    bad = {"reason": "r", "reasonings": [{"label": "", "body": "b"}]}
    with pytest.raises(ValidationError):
        _validate(
            {"stocks": [{"company_id": SAMSUNG, "buy": bad, "sell": SIDE}, VALID["stocks"][1]]}
        )


def test_a_whitespace_only_label_is_rejected():
    bad = {"reason": "r", "reasonings": [{"label": "   ", "body": "b"}]}
    with pytest.raises(ValidationError):
        _validate(
            {"stocks": [{"company_id": SAMSUNG, "buy": bad, "sell": SIDE}, VALID["stocks"][1]]}
        )


def _scripted(*answers):
    seen = []
    answers = iter(answers)

    def answer(messages):
        seen.append(messages)
        return next(answers)

    return RunnableLambda(answer), seen


def _ok(raw):
    return {"raw": None, "parsed": Explanations.model_validate(raw), "parsing_error": None}


def test_explain_returns_a_valid_first_answer():
    structured, seen = _scripted(_ok(VALID))

    result = explain(structured, TARGETS, TRACE, result_chars=10)

    assert [s.company_id for s in result.stocks] == [SAMSUNG, HYNIX]
    prompt = seen[0][1].content
    assert "삼성전자" in prompt and "SK하이닉스" in prompt
    assert "x" * 10 in prompt and "x" * 11 not in prompt


def test_explain_feeds_coverage_errors_back_once():
    incomplete = {"stocks": VALID["stocks"][:1]}
    structured, seen = _scripted(_ok(incomplete), _ok(VALID))

    result = explain(structured, TARGETS, TRACE, result_chars=10)

    assert len(result.stocks) == 2
    assert isinstance(seen[1][-1], HumanMessage)
    assert f"{HYNIX}: missing" in seen[1][-1].content


def test_an_unparsed_answer_is_retried_then_rejected():
    unparsed = {"raw": None, "parsed": None, "parsing_error": ValueError("bad json")}
    structured, seen = _scripted(unparsed, unparsed)

    with pytest.raises(ExplanationRejected, match="bad json"):
        explain(structured, TARGETS, TRACE, result_chars=10)
    assert len(seen) == 2


def test_load_targets_reads_holdings_exits_and_previous_weights(engine):
    with engine.begin() as conn:
        previous = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.7, 'c', 'm') RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
                " VALUES (:p, :c, 0.3)"
            ),
            {"p": previous, "c": HYNIX},
        )
        current = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.4, 'c', 'm') RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
                " VALUES (:p, :c, 0.6)"
            ),
            {"p": current, "c": SAMSUNG},
        )
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
                " VALUES (:p, :c, 'r')"
            ),
            {"p": current, "c": HYNIX},
        )

    assert load_targets(engine, current) == TARGETS


@pytest.mark.parametrize(
    ("scope", "initial", "shared"),
    [
        ("common", False, False),
        ("common", True, False),
        ("participant", False, False),
        ("participant", True, False),
        ("participant", False, True),
        ("participant", True, True),
    ],
)
def test_explanation_weights_follow_generation_history_across_interleaved_portfolios(
    engine, _request, _insert_portfolio, scope, initial, shared
):
    participants = [store_settings(engine, _request(participant_id=pid))[0] for pid in (1, 2, 3)]
    history = []
    previous = None
    if scope != "common" or not initial:
        common = _insert_portfolio(engine)
        history.append((common, 0.1, 0.15))
        if scope == "common":
            previous = common
    if scope != "participant" or not initial:
        mine = _insert_portfolio(engine)
        link_portfolio(engine, 1, mine)
        history.append((mine, 0.2, 0.25))
        if scope == "participant":
            previous = mine
            if shared:
                link_portfolio(engine, 3, mine)
        later_mine = _insert_portfolio(engine)
        link_portfolio(engine, 1, later_mine)
        history.append((later_mine, 0.5, 0.35))
    other = _insert_portfolio(engine)
    link_portfolio(engine, 2, other)
    history.append((other, 0.8, 0.05))
    current = _insert_portfolio(engine)
    claim = None
    if scope == "participant":
        key = generation_key(participants[0], previous, "m")
        claim = claim_job(engine, key, participants[0], previous, 3600, 3)
        assert claim is not None and claim.mine
        link_portfolio(engine, 1, current)
    with engine.begin() as conn:
        for portfolio_id, holding_weight, exit_weight in history:
            conn.execute(
                sa.text(
                    "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
                    " VALUES (:id, :holding, :holding_weight), (:id, :exit, :exit_weight)"
                ),
                dict(
                    id=portfolio_id,
                    holding=SAMSUNG,
                    exit=HYNIX,
                    holding_weight=holding_weight,
                    exit_weight=exit_weight,
                ),
            )
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
                " VALUES (:id, :company, 0.3)"
            ),
            {"id": current, "company": SAMSUNG},
        )
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
                " VALUES (:id, :company, 'r')"
            ),
            {"id": current, "company": HYNIX},
        )
        if claim is not None:
            assert finish_job(conn, claim, current)

    expected = (None, None) if initial else ((0.1, 0.15) if scope == "common" else (0.2, 0.25))
    targets = load_targets(engine, current)
    assert tuple(target.previous_weight for target in targets) == expected
    if shared:
        link_portfolio(engine, 3, current)
        assert load_targets(engine, current) == targets
    structured, seen = _scripted(_ok(VALID))
    explain(structured, targets, TRACE, result_chars=10)
    assert f"previously {expected[0]}" in seen[0][1].content
    assert f"was {expected[1]}" in seen[0][1].content


def test_save_explanations_writes_one_row_per_side(engine):
    with engine.begin() as conn:
        portfolio_id = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.4, 'c', 'm') RETURNING id"
            )
        ).scalar_one()

    save_explanations(engine, portfolio_id, _validate(VALID))

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT company_id, side, reason, reasonings FROM portfolio_reasons"
                " ORDER BY company_id, side"
            )
        ).all()
    assert [(r.company_id, r.side) for r in rows] == [
        (SAMSUNG, "buy"),
        (SAMSUNG, "sell"),
        (HYNIX, "sell"),
    ]
    assert rows[0].reasonings == [{"label": "HBM", "body": "늘었어요."}]
    assert _status(engine, portfolio_id) == "ready"


def test_save_explanations_handles_empty_stocks(engine):
    with engine.begin() as conn:
        portfolio_id = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (1.0, 'c', 'm') RETURNING id"
            )
        ).scalar_one()

    save_explanations(engine, portfolio_id, Explanations(stocks=[]))

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT COUNT(*) FROM portfolio_reasons WHERE portfolio_id = :id"),
            {"id": portfolio_id},
        ).scalar_one()
    assert rows == 0
    assert _status(engine, portfolio_id) == "ready"


def _status(engine, portfolio_id):
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT status FROM portfolios WHERE id = :id"), {"id": portfolio_id}
        ).scalar_one()


def _insert(engine, trace):
    with engine.begin() as conn:
        return conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model, trace)"
                " VALUES (1.0, 'c', 'm', CAST(:t AS jsonb)) RETURNING id"
            ),
            {"t": None if trace is None else json.dumps([e.model_dump() for e in trace])},
        ).scalar_one()


def test_nothing_is_unexplained_without_a_portfolio(engine):
    assert load_unexplained(engine) is None


def test_a_new_portfolio_is_pending_and_unexplained(engine):
    portfolio_id = _insert(engine, TRACE)

    assert _status(engine, portfolio_id) == "explanation_pending"
    assert load_unexplained(engine) == (portfolio_id, TRACE)


def test_a_failed_portfolio_is_unexplained_until_its_explanation_is_saved(engine):
    portfolio_id = _insert(engine, TRACE)
    mark_explanation_failed(engine, portfolio_id)

    assert _status(engine, portfolio_id) == "explanation_failed"
    assert load_unexplained(engine) == (portfolio_id, TRACE)

    save_explanations(engine, portfolio_id, Explanations(stocks=[]))

    assert load_unexplained(engine) is None


def test_only_the_latest_portfolio_is_retried(engine):
    _insert(engine, TRACE)
    latest = _insert(engine, TRACE)
    save_explanations(engine, latest, Explanations(stocks=[]))

    assert load_unexplained(engine) is None


def test_a_portfolio_without_a_trace_is_not_retried(engine):
    _insert(engine, None)

    assert load_unexplained(engine) is None


def test_load_unexplained_ignores_a_competition_portfolio(engine, _request, _insert_portfolio):
    store_settings(engine, _request())
    competition = _insert_portfolio(engine, status="explanation_failed", trace=TRACE_ROW)
    link_portfolio(engine, 1, competition)

    assert load_unexplained(engine) is None


def test_load_unexplained_still_finds_a_common_portfolio(engine, _request, _insert_portfolio):
    store_settings(engine, _request())
    common = _insert_portfolio(engine, status="explanation_failed", trace=TRACE_ROW)
    linked = _insert_portfolio(engine, status="explanation_failed", trace=TRACE_ROW)
    link_portfolio(engine, 1, linked)

    portfolio_id, _ = load_unexplained(engine)

    assert portfolio_id == common


def _portfolio_with_one_holding(engine):
    with engine.begin() as conn:
        portfolio_id = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.4, 'c', 'm') RETURNING id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight)"
                " VALUES (:p, :c, 0.6)"
            ),
            {"p": portfolio_id, "c": SAMSUNG},
        )
    return portfolio_id


def _stub_explainer_for(engine, portfolio_id):
    targets = load_targets(engine, portfolio_id)
    raw = {"stocks": [{"company_id": t.company_id, "buy": SIDE, "sell": SIDE} for t in targets]}
    structured, _ = _scripted(_ok(raw))
    return structured


def test_explanation_writes_a_reason_per_side(engine):
    portfolio_id = _portfolio_with_one_holding(engine)
    explainer = _stub_explainer_for(engine, portfolio_id)

    explanations = explain(explainer, load_targets(engine, portfolio_id), [], 2000)
    save_explanations(engine, portfolio_id, explanations)

    with engine.connect() as conn:
        sides = (
            conn.execute(
                sa.text(
                    "SELECT side FROM portfolio_reasons WHERE portfolio_id = :id ORDER BY side"
                ),
                {"id": portfolio_id},
            )
            .scalars()
            .all()
        )
    assert len(explanations.stocks) == 1
    assert sides == ["buy", "sell"]


def test_explanation_propagates_a_provider_failure(engine):
    portfolio_id = _portfolio_with_one_holding(engine)
    failing = Mock(invoke=Mock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        explain(failing, load_targets(engine, portfolio_id), [], 2000)
