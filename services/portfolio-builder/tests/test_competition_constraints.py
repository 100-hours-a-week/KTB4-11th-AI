from portfolio_builder.participants.portfolio import CompetitionSubmission as Submission
from portfolio_builder.participants.portfolio import Constraints, validate_constraints
from portfolio_builder.portfolio import Holding, normalize_weights


def _submission(holdings, cash_weight=1.0, **constraints):
    return Submission(
        holdings=[
            Holding(company_id=c, weight=w, reason="r", cited_cluster_ids=[]) for c, w in holdings
        ],
        exits=[],
        cash_weight=cash_weight,
        commentary="c",
        constraints=Constraints(**constraints),
    )


def test_no_declared_constraints_reject_nothing():
    assert validate_constraints(_submission([("a", 0.9)], cash_weight=0.1)) == []


def test_too_many_holdings_is_rejected():
    submission = _submission([("a", 0.3), ("b", 0.3)], cash_weight=0.4, max_holdings=1)
    assert validate_constraints(submission) == ["constraints: 2 holdings exceeds max_holdings 1"]


def test_an_overweight_holding_is_rejected():
    submission = _submission([("a", 0.5)], cash_weight=0.5, max_weight_per_holding=0.25)
    assert len(validate_constraints(submission)) == 1


def test_too_little_cash_is_rejected():
    submission = _submission([("a", 0.9)], cash_weight=0.1, min_cash_weight=0.2)
    assert len(validate_constraints(submission)) == 1


def test_limits_are_measured_against_normalised_weights_including_cash():
    raw = _submission([("a", 3.0)], cash_weight=1.0, max_weight_per_holding=0.8)
    assert validate_constraints(normalize_weights(raw)) == []
    stricter = _submission([("a", 3.0)], cash_weight=1.0, max_weight_per_holding=0.7)
    assert len(validate_constraints(normalize_weights(stricter))) == 1


def test_a_holding_exactly_at_max_weight_per_holding_passes():
    submission = _submission([("a", 0.5)], cash_weight=0.5, max_weight_per_holding=0.5)
    assert validate_constraints(submission) == []


def test_three_equal_holdings_exactly_at_one_third_each_passes():
    submission = _submission(
        [("a", 0.33), ("b", 0.33), ("c", 0.33)],
        cash_weight=0.0,
        max_weight_per_holding=1 / 3,
    )
    assert validate_constraints(normalize_weights(submission)) == []


def test_a_holding_clearly_over_max_weight_per_holding_still_fails():
    submission = _submission([("a", 0.51)], cash_weight=0.49, max_weight_per_holding=0.5)
    assert len(validate_constraints(submission)) == 1


def test_cash_exactly_at_min_cash_weight_passes():
    submission = _submission([("a", 0.8)], cash_weight=0.2, min_cash_weight=0.2)
    assert validate_constraints(submission) == []


def test_cash_clearly_below_min_cash_weight_still_fails():
    submission = _submission([("a", 0.81)], cash_weight=0.19, min_cash_weight=0.2)
    assert len(validate_constraints(submission)) == 1


def test_a_submission_satisfying_every_declared_constraint_passes():
    submission = _submission(
        [("a", 0.2), ("b", 0.2)],
        cash_weight=0.6,
        max_holdings=2,
        max_weight_per_holding=0.25,
        min_cash_weight=0.5,
    )
    assert validate_constraints(submission) == []
