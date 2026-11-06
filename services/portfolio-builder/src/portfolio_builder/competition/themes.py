from collections.abc import Sequence
from dataclasses import dataclass

import sqlalchemy as sa

from portfolio_builder.competition.message import AgentSettings


class UnknownTheme(Exception):
    pass


@dataclass(frozen=True)
class ResolvedAgent:
    competition_id: int
    user_id: int
    agent_id: int
    participant_id: int
    risk_profile: str
    interested_theme_codes: tuple[str, ...]
    excluded_theme_codes: tuple[str, ...]
    interested_theme_names: tuple[str, ...]
    excluded_theme_names: tuple[str, ...]
    additional_request: str


def theme_names(engine: sa.Engine, codes: Sequence[str]) -> dict[str, str]:
    unique_codes = list(dict.fromkeys(codes))
    if not unique_codes:
        return {}

    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT theme_code, name FROM themes WHERE theme_code IN :codes").bindparams(
                sa.bindparam("codes", expanding=True)
            ),
            {"codes": unique_codes},
        )
        name_by_code = {code: name for code, name in rows}

    missing = [code for code in unique_codes if code not in name_by_code]
    if missing:
        raise UnknownTheme(f"unknown theme code(s): {', '.join(missing)}")
    return name_by_code


def resolve_agent(
    engine: sa.Engine, competition_id: int, user_id: int, agent: AgentSettings
) -> ResolvedAgent:
    name_by_code = theme_names(engine, [*agent.preferred_themes, *agent.excluded_themes])

    excluded_codes = sorted(set(agent.excluded_themes))
    interested_codes = sorted(set(agent.preferred_themes) - set(excluded_codes))

    return ResolvedAgent(
        competition_id=competition_id,
        user_id=user_id,
        agent_id=agent.agent_id,
        participant_id=agent.participant_id,
        risk_profile=agent.risk_profile,
        interested_theme_codes=tuple(interested_codes),
        excluded_theme_codes=tuple(excluded_codes),
        interested_theme_names=tuple(name_by_code[code] for code in interested_codes),
        excluded_theme_names=tuple(name_by_code[code] for code in excluded_codes),
        additional_request=agent.additional_request,
    )
