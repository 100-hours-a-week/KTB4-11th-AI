from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa

from portfolio_builder.competition.message import AgentSettings
from portfolio_builder.database import like_group_prefix, like_group_suffix


class UnresolvedTheme(Exception):
    pass


@dataclass(frozen=True)
class ResolvedAgent:
    competition_id: int
    user_id: int
    agent_id: int
    participant_id: int
    risk_profile: str
    interested_theme_codes: list[str]
    excluded_theme_codes: list[str]
    interested_theme_names: list[str]
    excluded_theme_names: list[str]
    additional_request: str


def _codes_matching(conn: sa.Connection, pattern: str) -> list[str]:
    rows = conn.execute(
        sa.text(
            "SELECT theme_code FROM themes WHERE name LIKE :pattern ESCAPE '\\' ORDER BY theme_code"
        ),
        {"pattern": pattern},
    )
    return list(rows.scalars())


def resolve_theme_codes(engine: sa.Engine, names: Sequence[str]) -> dict[str, list[str]]:
    unique_names = list(dict.fromkeys(names))
    if not unique_names:
        return {}

    resolved: dict[str, list[str]] = {}
    with engine.connect() as conn:
        exact_rows = conn.execute(
            sa.text(
                "SELECT name, theme_code FROM themes WHERE name IN :names ORDER BY theme_code"
            ).bindparams(sa.bindparam("names", expanding=True)),
            {"names": unique_names},
        )
        for name, code in exact_rows:
            resolved.setdefault(name, []).append(code)

        for name in unique_names:
            if name in resolved:
                continue
            codes = _codes_matching(conn, like_group_prefix(name))
            if codes:
                resolved[name] = codes

        for name in unique_names:
            if name in resolved:
                continue
            codes = _codes_matching(conn, like_group_suffix(name))
            if len(codes) == 1:
                resolved[name] = codes

    missing = [name for name in unique_names if name not in resolved]
    if missing:
        raise UnresolvedTheme(f"unresolved theme name(s): {', '.join(missing)}")
    return resolved


def resolve_agent(
    engine: sa.Engine, competition_id: int, user_id: int, agent: AgentSettings
) -> ResolvedAgent:
    code_by_name = resolve_theme_codes(engine, [*agent.preferred_themes, *agent.excluded_themes])

    excluded_codes = sorted({code for name in agent.excluded_themes for code in code_by_name[name]})
    excluded_code_set = set(excluded_codes)
    interested_codes = sorted(
        {
            code
            for name in agent.preferred_themes
            for code in code_by_name[name]
            if code not in excluded_code_set
        }
    )

    excluded_names = set(agent.excluded_themes)
    interested_names = sorted(
        {name for name in agent.preferred_themes if name not in excluded_names}
    )

    return ResolvedAgent(
        competition_id=competition_id,
        user_id=user_id,
        agent_id=agent.agent_id,
        participant_id=agent.participant_id,
        risk_profile=agent.risk_profile,
        interested_theme_codes=interested_codes,
        excluded_theme_codes=excluded_codes,
        interested_theme_names=interested_names,
        excluded_theme_names=sorted(excluded_names),
        additional_request=agent.additional_request,
    )
