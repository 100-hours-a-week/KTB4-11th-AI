from collections.abc import Sequence

import sqlalchemy as sa

from news_graph_builder.common import normalize
from news_graph_builder.graph.company_entities import find_stock_code, upsert_company_entity
from news_graph_builder.graph.dto import Entity
from news_graph_builder.graph.repository import upsert_plain_entity


def resolve(conn: sa.Connection, llm_entities: Sequence[Entity]) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for entity in llm_entities:
        name = normalize(entity.name)
        if not name or name in resolved:
            continue
        stock_code = find_stock_code(conn, alias=name)
        resolved[name] = (
            upsert_company_entity(conn, stock_code=stock_code)
            if stock_code is not None
            else upsert_plain_entity(
                conn, raw_name=entity.name.strip(), name=name, type_=entity.type.strip()
            )
        )
    return resolved
