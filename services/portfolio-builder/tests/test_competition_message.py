import pytest
from portfolio_builder.competition.message import (
    MAX_ADDITIONAL_REQUEST_CHARS,
    RISK_LEVEL_TO_PROFILE,
    AgentSettings,
    Request,
)
from pydantic import ValidationError

AGENT_BASE = {"agent_id": 101, "participant_id": 201, "risk_level": 1}
BASE = {"competition_id": 10, "user_id": 100, "agents": [AGENT_BASE]}


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
def test_every_risk_level_maps_to_the_right_profile(level):
    agent = AgentSettings(**{**AGENT_BASE, "risk_level": level})
    assert agent.risk_profile == RISK_LEVEL_TO_PROFILE[level]


def test_risk_level_direction_is_ascending_aggressiveness():
    assert RISK_LEVEL_TO_PROFILE == {
        1: "안정형",
        2: "안정추구형",
        3: "위험중립형",
        4: "성장추구형",
        5: "성장형",
    }


@pytest.mark.parametrize("level", [0, 6])
def test_risk_level_outside_one_to_five_is_rejected(level):
    with pytest.raises(ValidationError):
        AgentSettings(**{**AGENT_BASE, "risk_level": level})


def test_a_non_integer_risk_level_is_rejected():
    with pytest.raises(ValidationError):
        AgentSettings(**{**AGENT_BASE, "risk_level": "1"})


@pytest.mark.parametrize(
    "changes",
    [
        {"competition_id": "bad"},
        {"competition_id": "12"},
        {"user_id": None},
        {"user_id": 0},
        {"user_id": -1},
    ],
)
def test_invalid_envelope_identifiers_are_rejected(changes):
    with pytest.raises(ValidationError):
        Request(**{**BASE, **changes})


@pytest.mark.parametrize(
    "changes",
    [
        {"agent_id": "bad"},
        {"agent_id": 0},
        {"participant_id": None},
        {"participant_id": 0},
    ],
)
def test_invalid_agent_identifiers_are_rejected(changes):
    with pytest.raises(ValidationError):
        Request(**{**BASE, "agents": [{**AGENT_BASE, **changes}]})


def test_an_empty_agents_list_is_rejected():
    with pytest.raises(ValidationError):
        Request(**{**BASE, "agents": []})


def test_additional_request_defaults_to_empty_and_is_kept_verbatim():
    agent = AgentSettings(**AGENT_BASE)
    assert agent.additional_request == ""
    text = "  반도체만 담아줘\n"
    agent = AgentSettings(**{**AGENT_BASE, "additional_request": text})
    assert agent.additional_request == text


def test_additional_request_over_the_limit_is_rejected():
    with pytest.raises(ValidationError):
        AgentSettings(
            **{**AGENT_BASE, "additional_request": "가" * (MAX_ADDITIONAL_REQUEST_CHARS + 1)}
        )


def test_preferred_and_excluded_themes_default_to_empty_lists():
    agent = AgentSettings(**AGENT_BASE)
    assert agent.preferred_themes == []
    assert agent.excluded_themes == []


def test_a_batch_carries_every_agent():
    request = Request(
        **{
            **BASE,
            "agents": [
                AGENT_BASE,
                {**AGENT_BASE, "agent_id": 102, "participant_id": 202, "risk_level": 3},
            ],
        }
    )
    assert len(request.agents) == 2
    assert [agent.participant_id for agent in request.agents] == [201, 202]


def test_extra_fields_from_the_unified_creation_body_are_ignored_not_rejected():
    request = Request(
        **{
            **BASE,
            "competition_name": "2026 겨울 챔피언십",
            "competition_type": "AI_VS_AI",
            "start_date": "2026-12-01",
            "end_date": "2026-12-31",
            "initial_capital": 10000000,
            "ai_participant_count": 2,
        }
    )
    assert request.competition_id == BASE["competition_id"]
    assert request.user_id == BASE["user_id"]
    assert len(request.agents) == 1
    assert request.agents[0].participant_id == AGENT_BASE["participant_id"]
