import pytest
import sqlalchemy as sa
from competition.message import AgentSettings
from competition.themes import UnknownTheme, resolve_agent, theme_names

THEMES = [
    ("SG", "반도체_생산"),
    ("SH", "반도체_후공정장비"),
    ("SS", "반도체_시스템반도체"),
    ("BK", "은행"),
]


def _seed_themes(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO themes (theme_code, name) VALUES (:code, :name)"),
            [{"code": code, "name": name} for code, name in THEMES],
        )


def _agent(**overrides):
    base = {
        "agent_id": 1,
        "participant_id": 1,
        "risk_level": 5,
        "preferred_themes": [],
        "excluded_themes": [],
        "additional_request": "",
    }
    return AgentSettings(**{**base, **overrides})


def test_theme_names_reads_the_name_for_every_code(engine):
    _seed_themes(engine)

    assert theme_names(engine, ["SG", "BK"]) == {"SG": "반도체_생산", "BK": "은행"}


def test_theme_names_of_nothing_queries_nothing(engine):
    assert theme_names(engine, []) == {}


def test_a_repeated_code_is_read_once(engine):
    _seed_themes(engine)

    assert theme_names(engine, ["SG", "SG"]) == {"SG": "반도체_생산"}


def test_an_unknown_code_is_rejected_rather_than_dropped(engine):
    _seed_themes(engine)

    with pytest.raises(UnknownTheme) as unknown:
        theme_names(engine, ["SG", "NOPE"])

    assert "NOPE" in str(unknown.value)


def test_a_theme_name_sent_instead_of_a_code_is_rejected(engine):
    _seed_themes(engine)

    with pytest.raises(UnknownTheme):
        theme_names(engine, ["반도체_생산"])


def test_resolve_agent_stores_the_codes_and_their_names(engine):
    _seed_themes(engine)
    agent = _agent(preferred_themes=["SG", "SH"], excluded_themes=["BK"])

    resolved = resolve_agent(engine, 10, 100, agent)

    assert resolved.interested_theme_codes == ("SG", "SH")
    assert resolved.excluded_theme_codes == ("BK",)
    assert resolved.interested_theme_names == ("반도체_생산", "반도체_후공정장비")
    assert resolved.excluded_theme_names == ("은행",)


def test_a_code_in_both_lists_is_excluded_only(engine):
    _seed_themes(engine)
    agent = _agent(preferred_themes=["SG", "SH"], excluded_themes=["SG"])

    resolved = resolve_agent(engine, 10, 100, agent)

    assert resolved.excluded_theme_codes == ("SG",)
    assert resolved.interested_theme_codes == ("SH",)
    assert resolved.interested_theme_names == ("반도체_후공정장비",)


def test_resolve_agent_keeps_agent_and_participant_ids_distinct(engine):
    _seed_themes(engine)
    agent = _agent(agent_id=77, participant_id=88)

    resolved = resolve_agent(engine, 10, 100, agent)

    assert resolved.agent_id == 77
    assert resolved.participant_id == 88
