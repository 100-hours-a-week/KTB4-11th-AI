from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.extract import Entity
from news_graph_builder.normalize import normalize
from news_graph_builder.storage import company_aliases, company_entity_id, entities


def resolve(conn: sa.Connection, llm_entities: Sequence[Entity]) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for entity in llm_entities:
        name = normalize(entity.name)
        if not name or name in resolved:
            continue
        corp_code = conn.execute(
            sa.select(company_aliases.c.corp_code).where(company_aliases.c.alias == name)
        ).scalar()
        if corp_code is not None:
            resolved[name] = company_entity_id(conn, corp_code)
            continue
        statement = insert(entities).values(
            raw_name=entity.name.strip(), name=name, type=entity.type.strip()
        )
        resolved[name] = conn.execute(
            statement.on_conflict_do_update(
                index_elements=[entities.c.name, entities.c.type],
                index_where=entities.c.corp_code.is_(None),
                # A no-op update, so that RETURNING also yields the id of an existing row.
                set_={"name": statement.excluded.name},
            ).returning(entities.c.id)
        ).scalar_one()
    return resolved
