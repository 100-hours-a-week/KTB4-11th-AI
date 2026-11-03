from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from harness.contracts import DecisionContext, DecisionFailed, PortfolioProposal, PortfolioState
from harness.production import proposal_from_production
from harness.submission import SubmissionDesk, create_submission_app, submission_tools
from ktb_core.krx import KST

CONTEXT = DecisionContext(
    datetime(2026, 7, 6, 8, tzinfo=KST),
    PortfolioState(1_000.0, {"005930": 3}, {}),
    ("000660", "005930"),
)
VALID = {"holdings": [{"code": "005930", "weight": 2}], "cash_weight": 1}


@pytest.fixture
def desk():
    return SubmissionDesk()


@pytest.fixture
def client(desk):
    return TestClient(create_submission_app(desk))


def post(client, body, decision_id="d1"):
    return client.post("/portfolio", json=body, headers={"X-Decision-Id": decision_id})


def test_portfolio_shows_the_account_without_prices(desk, client):
    desk.open("d1", CONTEXT)
    body = client.get("/portfolio", headers={"X-Decision-Id": "d1"}).json()
    assert body == {
        "as_of": "2026-07-06T08:00:00+09:00",
        "universe": ["000660", "005930"],
        "cash": 1000.0,
        "holdings": {"005930": 3},
        "pending_order_values": {},
    }


def test_one_submission_is_accepted_and_duplicates_are_refused(desk, client):
    desk.open("d1", CONTEXT)
    assert post(client, VALID).status_code == 200
    duplicate = post(client, {**VALID, "cash_weight": 0})
    assert duplicate.status_code == 409
    assert duplicate.json()["status"] == "duplicate"
    window = desk.close()
    assert window.accepted == PortfolioProposal({"005930": 2.0}, 1.0)
    assert [a.status for a in window.attempts] == ["accepted", "duplicate"]


def test_invalid_submissions_report_every_error_and_allow_a_fix(desk, client):
    desk.open("d1", CONTEXT)
    bad = {
        "holdings": [
            {"code": "999999", "weight": 1},
            {"code": "005930", "weight": -1},
            {"code": "005930", "weight": 1},
        ],
        "cash_weight": 0,
    }
    response = post(client, bad)
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert "005930: appears more than once" in errors
    assert "999999: not in the universe" in errors
    assert post(client, {"holdings": "x"}).status_code == 422
    assert (
        client.post("/portfolio", content=b"{", headers={"X-Decision-Id": "d1"}).status_code == 422
    )
    assert post(client, VALID).status_code == 200
    assert [a.status for a in desk.close().attempts] == [
        "invalid",
        "invalid",
        "invalid",
        "accepted",
    ]


def test_stale_or_missing_decision_ids_are_refused(desk, client):
    assert post(client, VALID).status_code == 409
    desk.open("d1", CONTEXT)
    assert post(client, VALID, "old").status_code == 409
    assert client.post("/portfolio", json=VALID).status_code == 409
    assert client.get("/portfolio", headers={"X-Decision-Id": "old"}).status_code == 409
    assert desk.close().attempts == []


def test_only_one_window_is_open(desk):
    desk.open("d1", CONTEXT)
    with pytest.raises(RuntimeError):
        desk.open("d2", CONTEXT)
    desk.close()
    with pytest.raises(RuntimeError):
        desk.close()


def test_submission_tools_carry_the_desk_url():
    get, submit = submission_tools("http://runner:8100/")
    assert (get.method, get.url) == ("GET", "http://runner:8100/portfolio")
    assert (submit.method, submit.url) == ("POST", "http://runner:8100/portfolio")
    assert set(submit.required) <= set(submit.args)


def test_production_submissions_map_to_stock_codes():
    submission = {
        "holdings": [{"company_id": "00126380", "weight": 0.6, "cited_cluster_ids": [1]}],
        "exits": [{"company_id": "00164779", "reason": "r", "cited_cluster_ids": []}],
        "cash_weight": 0.4,
        "commentary": "c",
    }
    codes = {"00126380": "005930", "00164779": "000660"}
    assert proposal_from_production(submission, codes) == PortfolioProposal({"005930": 0.6}, 0.4)
    with pytest.raises(DecisionFailed) as failed:
        proposal_from_production(submission, {})
    assert failed.value.kind == "invalid_submission"
