from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.common import normalize
from news_graph_builder.company.dto import DartCompany
from news_graph_builder.database import (
    cluster_entities,
    companies,
    company_aliases,
    entities,
    relations,
)

COMPANY_TYPE = "기업"


def has_companies(conn: sa.Connection) -> bool:
    return conn.execute(sa.select(companies.c.corp_code).limit(1)).first() is not None


def find_corp_code(conn: sa.Connection, *, alias: str) -> str | None:
    return conn.execute(
        sa.select(company_aliases.c.corp_code).where(company_aliases.c.alias == alias)
    ).scalar()


def upsert_company_entity(conn: sa.Connection, *, corp_code: str) -> int:
    corp_name = conn.execute(
        sa.select(companies.c.corp_name).where(companies.c.corp_code == corp_code)
    ).scalar_one()
    statement = insert(entities).values(
        raw_name=corp_name, name=normalize(corp_name), type=COMPANY_TYPE, corp_code=corp_code
    )
    return conn.execute(
        statement.on_conflict_do_update(
            index_elements=[entities.c.corp_code],
            index_where=entities.c.corp_code.is_not(None),
            # A no-op update, so that RETURNING also yields the id of an existing row.
            set_={"corp_code": statement.excluded.corp_code},
        ).returning(entities.c.id)
    ).scalar_one()


def upsert_companies(conn: sa.Connection, rows: Sequence[DartCompany]) -> None:
    statement = insert(companies).values(
        [
            {
                "corp_code": company.corp_code,
                "stock_code": company.stock_code,
                "corp_name": company.corp_name,
                "corp_eng_name": company.corp_eng_name,
            }
            for company in rows
        ]
    )
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=[companies.c.corp_code],
            set_={
                "stock_code": statement.excluded.stock_code,
                "corp_name": statement.excluded.corp_name,
                "corp_eng_name": statement.excluded.corp_eng_name,
                "synced_at": sa.func.now(),
            },
        )
    )


def insert_aliases(conn: sa.Connection, aliases: Sequence[tuple[str, str]]) -> None:
    conn.execute(
        insert(company_aliases)
        .values([{"alias": alias, "corp_code": corp_code} for alias, corp_code in aliases])
        .on_conflict_do_nothing()
    )


def find_plain_entities_matching_aliases(conn: sa.Connection) -> list[tuple[int, str]]:
    query = (
        sa.select(entities.c.id, company_aliases.c.corp_code)
        .join(company_aliases, company_aliases.c.alias == entities.c.name)
        .where(entities.c.corp_code.is_(None))
    )
    return [(row.id, row.corp_code) for row in conn.execute(query)]


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
