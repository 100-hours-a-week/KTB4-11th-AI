from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from news_graph_builder.dart import DartCompany
from news_graph_builder.normalize import normalize
from news_graph_builder.storage import (
    cluster_entities,
    companies,
    company_aliases,
    company_entity_id,
    entities,
    relations,
)


def sync_companies(
    conn: sa.Connection, kospi: Sequence[tuple[str, str]], dart: Sequence[DartCompany]
) -> tuple[int, int]:
    by_stock_code = {company.stock_code: company for company in dart}
    joined = {
        by_stock_code[code].corp_code: (by_stock_code[code], name)
        for code, name in kospi
        if code in by_stock_code
    }
    if not joined:
        raise ValueError(f"none of {len(kospi)} KOSPI codes matched a DART stock_code")

    statement = insert(companies).values(
        [
            {
                "corp_code": company.corp_code,
                "stock_code": company.stock_code,
                "corp_name": company.corp_name,
                "corp_eng_name": company.corp_eng_name,
            }
            for company, _ in joined.values()
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
    aliases = [
        {"alias": alias, "corp_code": company.corp_code}
        for company, kiwoom_name in joined.values()
        for alias in dict.fromkeys(
            normalize(name)
            for name in (
                company.corp_name,
                kiwoom_name,
                company.corp_eng_name or "",
            )
        )
        if alias
    ]
    conn.execute(insert(company_aliases).values(aliases).on_conflict_do_nothing())

    plain = conn.execute(
        sa.select(entities.c.id, company_aliases.c.corp_code)
        .join(company_aliases, company_aliases.c.alias == entities.c.name)
        .where(entities.c.corp_code.is_(None))
    ).all()
    for entity_id, corp_code in plain:
        company_id = company_entity_id(conn, corp_code)
        for column in (relations.c.source_entity_id, relations.c.target_entity_id):
            conn.execute(
                sa.update(relations).where(column == entity_id).values({column: company_id})
            )
        conn.execute(
            insert(cluster_entities)
            .from_select(
                ["cluster_id", "entity_id"],
                sa.select(
                    cluster_entities.c.cluster_id, sa.literal(company_id, sa.BigInteger)
                ).where(cluster_entities.c.entity_id == entity_id),
            )
            .on_conflict_do_nothing()
        )
        conn.execute(sa.delete(cluster_entities).where(cluster_entities.c.entity_id == entity_id))
        conn.execute(sa.delete(entities).where(entities.c.id == entity_id))
    return len(joined), len(plain)
