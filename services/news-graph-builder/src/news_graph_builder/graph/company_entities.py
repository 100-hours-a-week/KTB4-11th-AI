import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.common import normalize
from news_graph_builder.database import (
    cluster_entities,
    corporation_aliases,
    corporations,
    entities,
    relations,
)

COMPANY_TYPE = "기업"


def has_corporations(conn: sa.Connection) -> bool:
    return conn.execute(sa.select(corporations.c.stock_code).limit(1)).first() is not None


def find_stock_code(conn: sa.Connection, *, alias: str) -> str | None:
    return conn.execute(
        sa.select(corporation_aliases.c.stock_code).where(corporation_aliases.c.alias == alias)
    ).scalar()


def upsert_company_entity(conn: sa.Connection, *, stock_code: str) -> int:
    name = conn.execute(
        sa.select(corporations.c.name).where(corporations.c.stock_code == stock_code)
    ).scalar_one()
    statement = insert(entities).values(
        raw_name=name, name=normalize(name), type=COMPANY_TYPE, stock_code=stock_code
    )
    return conn.execute(
        statement.on_conflict_do_update(
            index_elements=[entities.c.stock_code],
            index_where=entities.c.stock_code.is_not(None),
            # A no-op update, so that RETURNING also yields the id of an existing row.
            set_={"stock_code": statement.excluded.stock_code},
        ).returning(entities.c.id)
    ).scalar_one()


def find_plain_entities_matching_aliases(conn: sa.Connection) -> list[tuple[int, str]]:
    query = (
        sa.select(entities.c.id, corporation_aliases.c.stock_code)
        .join(corporation_aliases, corporation_aliases.c.alias == entities.c.name)
        .where(entities.c.stock_code.is_(None))
    )
    return [(row.id, row.stock_code) for row in conn.execute(query)]


def merge_entity(conn: sa.Connection, *, source_id: int, target_id: int) -> None:
    for column in (relations.c.source_entity_id, relations.c.target_entity_id):
        conn.execute(sa.update(relations).where(column == source_id).values({column: target_id}))
    conn.execute(
        insert(cluster_entities)
        .from_select(
            ["cluster_id", "entity_id"],
            sa.select(cluster_entities.c.cluster_id, sa.literal(target_id, sa.BigInteger)).where(
                cluster_entities.c.entity_id == source_id
            ),
        )
        .on_conflict_do_nothing()
    )
    conn.execute(sa.delete(cluster_entities).where(cluster_entities.c.entity_id == source_id))
    conn.execute(sa.delete(entities).where(entities.c.id == source_id))


def merge_company_entities(conn: sa.Connection) -> int:
    plain = find_plain_entities_matching_aliases(conn)
    for entity_id, stock_code in plain:
        company_id = upsert_company_entity(conn, stock_code=stock_code)
        merge_entity(conn, source_id=entity_id, target_id=company_id)
    return len(plain)
