from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from portfolio_builder.errors import PortfolioRejected
from portfolio_builder.portfolio import (
    Exit,
    Holding,
    Submission,
    normalize_weights,
    save_portfolio,
    validate_portfolio,
)

SAMSUNG, HYNIX = "00126380", "00164779"

BASE = Submission(
    holdings=[Holding(company_id="A", weight=1, reason="news", cited_cluster_ids=[1])],
    exits=[],
    cash_weight=0,
    commentary="overall",
)


def test_accepts_a_first_portfolio_whose_entries_all_have_reasons():
    assert validate_portfolio(BASE, frozenset()) == []


def test_a_held_company_keeps_its_place_without_a_new_reason():
    submission = BASE.model_copy(
        update={"holdings": [Holding(company_id="A", weight=1, cited_cluster_ids=[])]}
    )
    assert validate_portfolio(submission, frozenset({"A"})) == []


def test_an_exited_previous_holding_needs_only_its_exit():
    submission = BASE.model_copy(
        update={"exits": [Exit(company_id="B", reason="guidance cut", cited_cluster_ids=[2])]}
    )
    assert validate_portfolio(submission, frozenset({"B"})) == []


def test_reports_every_problem_at_once_in_order():
    submission = Submission(
        holdings=[
            Holding(company_id="A", weight=-1, cited_cluster_ids=[]),
            Holding(company_id="A", weight=0, reason="dup", cited_cluster_ids=[]),
            Holding(company_id="C", weight=0, reason="  ", cited_cluster_ids=[]),
        ],
        exits=[
            Exit(company_id="A", reason="both", cited_cluster_ids=[]),
            Exit(company_id="Z", reason="", cited_cluster_ids=[]),
        ],
        cash_weight=-0.5,
        commentary=" ",
    )

    assert validate_portfolio(submission, frozenset({"B"})) == [
        "holdings: A weight must be a finite number >= 0",
        "holdings: A is entering the portfolio and needs a reason",
        "holdings: A appears more than once",
        "holdings: C is entering the portfolio and needs a reason",
        "exits: A is both held and exited",
        "exits: A was not in the previous portfolio",
        "exits: Z was not in the previous portfolio",
        "exits: Z needs a reason",
        "exits: B was held and is dropped, so it needs an exit with a reason",
        "cash_weight must be a finite number >= 0",
        "weights and cash_weight sum to 0 or less; at least one must be positive",
        "commentary must not be empty",
    ]


def test_normalize_weights_scales_holdings_and_cash_to_one():
    submission = BASE.model_copy(
        update={
            "holdings": [
                Holding(company_id="A", weight=3, reason="r", cited_cluster_ids=[]),
                Holding(company_id="B", weight=1, reason="r", cited_cluster_ids=[]),
            ],
            "cash_weight": 4,
        }
    )

    normalized = normalize_weights(submission)

    assert [h.weight for h in normalized.holdings] == [0.375, 0.125]
    assert normalized.cash_weight == 0.5
    assert submission.cash_weight == 4


SAVED = Submission(
    holdings=[
        Holding(company_id=SAMSUNG, weight=0.6, reason="HBM 공급", cited_cluster_ids=[1]),
        Holding(company_id=HYNIX, weight=0.2, cited_cluster_ids=[]),
    ],
    exits=[],
    cash_weight=0.2,
    commentary="총평",
)


def _count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT count(*) FROM portfolios")).scalar_one()


def test_save_writes_the_portfolio_holdings_and_citations(engine, news_client):
    portfolio_id = save_portfolio(engine, SAVED, "openrouter/openai/gpt-5.5", news_client)

    with engine.connect() as conn:
        portfolio = conn.execute(
            sa.text("SELECT cash_weight, commentary, model FROM portfolios WHERE id = :id"),
            {"id": portfolio_id},
        ).one()
        holdings = conn.execute(
            sa.text(
                "SELECT company_id, weight, reason, cited_cluster_ids FROM portfolio_holdings"
                " WHERE portfolio_id = :id ORDER BY company_id"
            ),
            {"id": portfolio_id},
        ).all()
    assert tuple(portfolio) == (0.2, "총평", "openrouter/openai/gpt-5.5")
    assert [tuple(h) for h in holdings] == [
        (SAMSUNG, 0.6, "HBM 공급", [1]),
        (HYNIX, 0.2, None, []),
    ]


def test_save_rolls_back_an_unknown_company_with_a_readable_error(engine, news_client):
    bad = SAVED.model_copy(
        update={
            "holdings": [Holding(company_id="99999999", weight=1, reason="x", cited_cluster_ids=[])]
        }
    )

    with pytest.raises(PortfolioRejected) as rejected:
        save_portfolio(engine, bad, "m", news_client)

    assert "99999999" in " ".join(rejected.value.errors)
    assert _count(engine) == 0


def test_save_rejects_an_unknown_cited_cluster_before_writing(engine, news_client):
    bad = SAVED.model_copy(
        update={
            "holdings": [
                Holding(company_id=SAMSUNG, weight=1, reason="x", cited_cluster_ids=[1, 404])
            ]
        }
    )

    with pytest.raises(PortfolioRejected) as rejected:
        save_portfolio(engine, bad, "m", news_client)

    assert rejected.value.errors == ["cited_cluster_ids not found: 404"]
    assert _count(engine) == 0


def test_save_checks_citations_before_opening_the_portfolio_transaction():
    engine = Mock()
    news_client = Mock()
    news_client.has_cluster.return_value = False
    bad = SAVED.model_copy(
        update={
            "holdings": [Holding(company_id=SAMSUNG, weight=1, reason="x", cited_cluster_ids=[404])]
        }
    )

    with pytest.raises(PortfolioRejected, match="cited_cluster_ids not found: 404"):
        save_portfolio(engine, bad, "m", news_client)

    engine.begin.assert_not_called()


@pytest.mark.parametrize("weight", [float("inf"), float("nan")])
def test_rejects_a_non_finite_weight(weight):
    submission = BASE.model_copy(
        update={
            "holdings": [
                Holding(
                    company_id="A",
                    weight=weight,
                    reason="r",
                    cited_cluster_ids=[],
                )
            ]
        }
    )
    assert "holdings: A weight must be a finite number >= 0" in validate_portfolio(
        submission, frozenset()
    )


def test_rejects_weights_whose_sum_overflows():
    submission = BASE.model_copy(
        update={
            "holdings": [
                Holding(company_id="A", weight=1e308, reason="r", cited_cluster_ids=[]),
                Holding(company_id="B", weight=1e308, reason="r", cited_cluster_ids=[]),
            ]
        }
    )
    assert validate_portfolio(submission, frozenset()) == [
        "weights and cash_weight must add up to a finite number"
    ]


def test_save_trace_writes_the_entries_as_json(engine):
    from portfolio_builder.agent.trace import TraceEntry
    from portfolio_builder.portfolio import save_trace

    with engine.begin() as conn:
        portfolio_id = conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model)"
                " VALUES (0.1, 'c', 'm') RETURNING id"
            )
        ).scalar_one()

    save_trace(
        engine,
        portfolio_id,
        [
            TraceEntry(
                turn=1,
                kind="tool",
                name="search_news_cluster",
                args={"q": "HBM"},
                result="r",
            )
        ],
    )

    with engine.connect() as conn:
        stored = conn.execute(
            sa.text("SELECT trace FROM portfolios WHERE id = :id"), {"id": portfolio_id}
        ).scalar_one()
    assert stored == [
        {
            "turn": 1,
            "kind": "tool",
            "reasoning": None,
            "text": None,
            "name": "search_news_cluster",
            "args": {"q": "HBM"},
            "result": "r",
        }
    ]
