from collections.abc import Sequence

import sqlalchemy as sa

from news_graph_builder.common import normalize
from news_graph_builder.company.dto import DartCompany
from news_graph_builder.company.repository import (
    aliased_plain_entities,
    company_entity_id,
    insert_aliases,
    merge_entity,
    upsert_companies,
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

    upsert_companies(conn, [company for company, _ in joined.values()])
    insert_aliases(
        conn,
        [
            (alias, company.corp_code)
            for company, kiwoom_name in joined.values()
            for alias in dict.fromkeys(
                normalize(name)
                for name in (company.corp_name, kiwoom_name, company.corp_eng_name or "")
            )
            if alias
        ],
    )

    plain = aliased_plain_entities(conn)
    for entity_id, corp_code in plain:
        merge_entity(conn, entity_id, company_entity_id(conn, corp_code))
    return len(joined), len(plain)
