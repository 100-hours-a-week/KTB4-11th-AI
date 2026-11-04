import pytest
from portfolio_builder.competition.message import MAX_ADDITIONAL_REQUEST_CHARS, Request
from pydantic import ValidationError

PROFILES = ["성장형", "성장추구형", "위험중립형", "안정추구형", "안정형"]
BASE = {"competition_id": 10, "participant_id": 1, "user_id": 100, "risk_profile": "성장형"}


@pytest.mark.parametrize("profile", PROFILES)
def test_every_risk_profile_validates(profile):
    assert Request(**{**BASE, "risk_profile": profile}).risk_profile == profile


@pytest.mark.parametrize("profile", ["성장 추구형", "위험 중립형", "안정 추구형", "", "growth"])
def test_spaced_or_unknown_risk_profile_is_rejected(profile):
    with pytest.raises(ValidationError):
        Request(**{**BASE, "risk_profile": profile})


@pytest.mark.parametrize(
    "changes",
    [
        {"competition_id": "bad"},
        {"competition_id": "12"},
        {"participant_id": None},
        {"participant_id": 0},
        {"user_id": -1},
    ],
)
def test_invalid_identifiers_are_rejected(changes):
    with pytest.raises(ValidationError):
        Request(**{**BASE, **changes})


def test_additional_request_defaults_to_empty_and_is_kept_verbatim():
    assert Request(**BASE).additional_request == ""
    text = "  반도체만 담아줘\n"
    assert Request(**{**BASE, "additional_request": text}).additional_request == text


def test_additional_request_over_the_limit_is_rejected():
    with pytest.raises(ValidationError):
        Request(**{**BASE, "additional_request": "가" * (MAX_ADDITIONAL_REQUEST_CHARS + 1)})


def test_a_theme_in_both_lists_is_excluded_and_not_interested():
    request = Request(**BASE, interested_theme_codes=["T1", "T2"], excluded_theme_codes=["T2"])
    assert request.effective_interested == ["T1"]
    assert request.effective_excluded == ["T2"]


def test_duplicate_theme_codes_collapse_and_sort():
    request = Request(**BASE, interested_theme_codes=["T2", "T1", "T2"])
    assert request.effective_interested == ["T1", "T2"]
