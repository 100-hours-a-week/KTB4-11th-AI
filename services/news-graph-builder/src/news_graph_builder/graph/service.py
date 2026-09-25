from collections.abc import Sequence

import sqlalchemy as sa

from news_graph_builder.common import normalize
from news_graph_builder.company import company_entity_id, find_corp_code
from news_graph_builder.graph.dto import Entity
from news_graph_builder.graph.repository import upsert_plain_entity


def resolve(conn: sa.Connection, llm_entities: Sequence[Entity]) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for entity in llm_entities:
        name = normalize(entity.name)
        if not name or name in resolved:
            continue
        corp_code = find_corp_code(conn, name)
        resolved[name] = (
            company_entity_id(conn, corp_code)
            if corp_code is not None
            else upsert_plain_entity(conn, entity.name.strip(), name, entity.type.strip())
        )
    return resolved
