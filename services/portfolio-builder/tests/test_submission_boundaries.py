from portfolio_builder.portfolio import Submission
from portfolio_builder.tools.submit import SubmitPortfolioArgs


def test_common_submission_has_no_participant_constraints():
    assert set(Submission.model_fields) == {"holdings", "exits", "cash_weight", "commentary"}
    assert "constraints" not in SubmitPortfolioArgs.model_fields


def test_competition_submission_exposes_its_own_constraints():
    from portfolio_builder.competition.submit import CompetitionSubmission, CompetitionSubmitArgs

    assert "constraints" in CompetitionSubmission.model_fields
    assert "constraints" in CompetitionSubmitArgs.model_fields
