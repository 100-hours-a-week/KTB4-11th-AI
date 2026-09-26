from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.common import normalize
from news_graph_builder.database import cluster_entities, cluster_summaries, entities, relations
from news_graph_builder.graph.dto import Extraction


def upsert_plain_entity(conn: sa.Connection, *, raw_name: str, name: str, type_: str) -> int:
    statement = insert(entities).values(raw_name=raw_name, name=name, type=type_)
    return conn.execute(
        statement.on_conflict_do_update(
            index_elements=[entities.c.name, entities.c.type],
            index_where=entities.c.corp_code.is_(None),
            # A no-op update, so that RETURNING also yields the id of an existing row.
            set_={"name": statement.excluded.name},
        ).returning(entities.c.id)
    ).scalar_one()


def write_graph(
    conn: sa.Connection,
    cluster_id: int,
    seen: datetime,
    extraction: Extraction,
    entity_ids: dict[str, int],
) -> int:
    conn.execute(sa.delete(cluster_entities).where(cluster_entities.c.cluster_id == cluster_id))
    conn.execute(sa.delete(relations).where(relations.c.cluster_id == cluster_id))
    if entity_ids:
        conn.execute(
            insert(cluster_entities),
            [
                {"cluster_id": cluster_id, "entity_id": entity_id}
                for entity_id in sorted(set(entity_ids.values()))
            ],
        )
    rows = [
        {
            "cluster_id": cluster_id,
            "source_entity_id": entity_ids[normalize(relation.source)],
            "target_entity_id": entity_ids[normalize(relation.target)],
            "type": relation.type,
            "description": relation.description,
        }
        for relation in extraction.relations
        if normalize(relation.source) in entity_ids and normalize(relation.target) in entity_ids
    ]
    if rows:
        conn.execute(insert(relations), rows)
    statement = insert(cluster_summaries).values(
        cluster_id=cluster_id,
        title=extraction.title,
        summary=extraction.summary,
        # Not now(): now() is this transaction's start time, and a clusterer update that
        # waited on lock_cluster's FOR SHARE lock can commit an older updated_at, which
        # would make the changed cluster look fresh.
        cluster_updated_at=seen,
    )
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=[cluster_summaries.c.cluster_id],
            set_={
                "title": statement.excluded.title,
                "summary": statement.excluded.summary,
                "cluster_updated_at": statement.excluded.cluster_updated_at,
                "summarized_at": sa.func.now(),
            },
        )
    )
    return len(extraction.relations) - len(rows)
