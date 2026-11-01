from harness.contracts import PortfolioProposal
from harness.validation import normalize, validate

UNIVERSE = ("005930", "000660")


def test_valid_proposal_has_no_errors():
    assert validate(PortfolioProposal({"005930": 1.0, "000660": 1.0}, 0.0), UNIVERSE) == []


def test_reports_every_error():
    errors = validate(
        PortfolioProposal({"999999": 1.0, "005930": -1.0, "000660": float("inf")}, -0.5), UNIVERSE
    )
    assert len(errors) == 4


def test_zero_total_is_an_error():
    assert validate(PortfolioProposal({"005930": 0.0}, 0.0), UNIVERSE) != []


def test_normalize_is_relative():
    assert normalize(PortfolioProposal({"005930": 3.0, "000660": 1.0}, 1.0)) == {
        "005930": 0.6,
        "000660": 0.2,
    }
