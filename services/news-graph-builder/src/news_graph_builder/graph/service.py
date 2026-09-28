from collections.abc import Sequence

import sqlalchemy as sa

from news_graph_builder.common import normalize
from news_graph_builder.company import find_corp_code, upsert_company_entity
from news_graph_builder.graph.dto import Entity
from news_graph_builder.graph.repository import upsert_plain_entity


def resolve(conn: sa.Connection, llm_entities: Sequence[Entity]) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for entity in llm_entities:
        name = normalize(entity.name)
        if not name or name in resolved:
            continue
        corp_code = find_corp_code(conn, alias=name)
        resolved[name] = (
            upsert_company_entity(conn, corp_code=corp_code)
            if corp_code is not None
            else upsert_plain_entity(
                conn, raw_name=entity.name.strip(), name=name, type_=entity.type.strip()
            )
        )
    return resolved
