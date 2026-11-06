import pytest
import sqlalchemy as sa
from portfolio_builder.competition.message import AgentSettings
from portfolio_builder.competition.repository import generation_key, store_settings
from portfolio_builder.competition.themes import (
    UnresolvedTheme,
    resolve_agent,
    resolve_theme_codes,
)
from portfolio_builder.errors import PortfolioRejected
from portfolio_builder.portfolio import excluded_company_ids
from portfolio_builder.tools.submit import submit_tool

LGES = "01515323"

GROUP_THEMES = [
    ("SG", "반도체_생산"),
    ("SH", "반도체_후공정장비"),
    ("SS", "반도체_시스템반도체"),
]
STANDALONE_THEME = ("BK", "은행")
# Starts with the group's prefix but has no separating underscore: a naive, unescaped
# `LIKE '반도체_%'` would still match it.
LOOKALIKE_THEME = ("SX", "반도체X")
SUFFIX_TWIN_THEME = ("CG", "자동차_생산")


def _seed_group_themes(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO themes (theme_code, name) VALUES (:code, :name)"),
            [{"code": code, "name": name} for code, name in [*GROUP_THEMES, STANDALONE_THEME]],
        )
        conn.execute(
            sa.text(
                "INSERT INTO theme_corporations (theme_code, stock_code) VALUES ('SG', '373220')"
            )
        )


def _seed_lookalike_theme(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO themes (theme_code, name) VALUES (:code, :name)"),
            {"code": LOOKALIKE_THEME[0], "name": LOOKALIKE_THEME[1]},
        )


def _seed_theme(engine, code, name):
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO themes (theme_code, name) VALUES (:code, :name)"),
            {"code": code, "name": name},
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


class Recorder:
    def info(self, event, **fields):
        pass

    def warning(self, event, **fields):
        pass

    def error(self, event, **fields):
        pass


def _call(args, call_id="c1"):
    return {"name": "submit_portfolio", "args": args, "id": call_id, "type": "tool_call"}


def test_an_exact_leaf_name_resolves_to_its_own_code(engine):
    _seed_group_themes(engine)

    resolved = resolve_theme_codes(engine, ["반도체_생산"])

    assert resolved == {"반도체_생산": ["SG"]}


def test_an_exact_standalone_name_resolves_to_its_own_code(engine):
    _seed_group_themes(engine)

    resolved = resolve_theme_codes(engine, ["은행"])

    assert resolved == {"은행": ["BK"]}


def test_a_group_name_resolves_to_every_code_in_the_group(engine):
    _seed_group_themes(engine)

    resolved = resolve_theme_codes(engine, ["반도체"])

    assert resolved == {"반도체": ["SG", "SH", "SS"]}


def test_the_prefix_match_escapes_the_underscore_and_skips_the_lookalike_name(engine):
    _seed_group_themes(engine)
    _seed_lookalike_theme(engine)

    resolved = resolve_theme_codes(engine, ["반도체"])

    assert resolved == {"반도체": ["SG", "SH", "SS"]}


def test_a_lookalike_name_resolves_only_to_itself(engine):
    _seed_group_themes(engine)
    _seed_lookalike_theme(engine)

    resolved = resolve_theme_codes(engine, ["반도체X"])

    assert resolved == {"반도체X": ["SX"]}


def test_a_bare_group_member_suffix_resolves_to_its_single_code(engine):
    _seed_group_themes(engine)

    resolved = resolve_theme_codes(engine, ["후공정장비"])

    assert resolved == {"후공정장비": ["SH"]}


def test_a_suffix_shared_by_two_groups_is_refused_rather_than_guessed(engine):
    _seed_group_themes(engine)
    _seed_theme(engine, *SUFFIX_TWIN_THEME)

    with pytest.raises(UnresolvedTheme):
        resolve_theme_codes(engine, ["생산"])


def test_an_exact_name_wins_over_the_same_string_as_a_suffix(engine):
    _seed_group_themes(engine)
    _seed_theme(engine, "PR", "생산")

    resolved = resolve_theme_codes(engine, ["생산"])

    assert resolved == {"생산": ["PR"]}


def test_a_group_name_wins_over_the_same_string_as_a_suffix(engine):
    _seed_group_themes(engine)
    _seed_theme(engine, "GB", "은행_반도체")

    resolved = resolve_theme_codes(engine, ["반도체"])

    assert resolved == {"반도체": ["SG", "SH", "SS"]}


def test_a_suffix_match_escapes_the_underscore_and_the_wildcard(engine):
    _seed_group_themes(engine)
    _seed_theme(engine, "PC", "소재_100%생산")
    _seed_theme(engine, "PD", "소재_1000생산")

    resolved = resolve_theme_codes(engine, ["100%생산"])

    assert resolved == {"100%생산": ["PC"]}


def test_an_unresolvable_name_raises(engine):
    _seed_group_themes(engine)

    with pytest.raises(UnresolvedTheme):
        resolve_theme_codes(engine, ["금융"])


def test_resolve_agent_excludes_a_group_resolved_name_from_interested(engine):
    _seed_group_themes(engine)
    agent = _agent(preferred_themes=["반도체"], excluded_themes=["반도체_생산"])

    resolved = resolve_agent(engine, 10, 100, agent)

    assert resolved.excluded_theme_codes == ["SG"]
    assert resolved.interested_theme_codes == ["SH", "SS"]
    assert resolved.interested_theme_names == ["반도체"]
    assert resolved.excluded_theme_names == ["반도체_생산"]


def test_resolve_agent_keeps_agent_and_participant_ids_distinct(engine):
    _seed_group_themes(engine)
    agent = _agent(agent_id=77, participant_id=88)

    resolved = resolve_agent(engine, 10, 100, agent)

    assert resolved.agent_id == 77
    assert resolved.participant_id == 88


def test_group_selection_and_leaf_selection_produce_the_same_generation_key(engine):
    _seed_group_themes(engine)
    by_group = resolve_agent(engine, 10, 100, _agent(participant_id=1, excluded_themes=["반도체"]))
    by_leaves = resolve_agent(
        engine,
        10,
        100,
        _agent(
            participant_id=2,
            excluded_themes=["반도체_생산", "반도체_후공정장비", "반도체_시스템반도체"],
        ),
    )

    assert sorted(by_group.excluded_theme_codes) == sorted(by_leaves.excluded_theme_codes)

    a, _ = store_settings(engine, by_group)
    b, _ = store_settings(engine, by_leaves)

    assert generation_key(a, None, "openrouter/m") == generation_key(b, None, "openrouter/m")


def test_a_holding_in_a_group_resolved_excluded_theme_is_rejected(engine, news_client):
    _seed_group_themes(engine)
    agent = _agent(excluded_themes=["반도체"])
    resolved = resolve_agent(engine, 10, 100, agent)
    excluded = excluded_company_ids(engine, resolved.excluded_theme_codes)
    assert LGES in excluded

    tool = submit_tool(engine, frozenset(), "m", news_client, Recorder(), excluded=excluded)
    holding = {"company_id": LGES, "weight": 1, "reason": "r", "cited_cluster_ids": []}
    submission = {"holdings": [holding], "exits": [], "cash_weight": 0, "commentary": "c"}

    with pytest.raises(PortfolioRejected) as rejected:
        tool.invoke(_call(submission))

    assert f"holdings: {LGES} is in an excluded theme" in rejected.value.errors
