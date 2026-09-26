from collections.abc import Sequence

import sqlalchemy as sa

from news_graph_builder.common import normalize
from news_graph_builder.company.dto import DartCompany
from news_graph_builder.company.repository import (
    find_plain_entities_matching_aliases,
    insert_aliases,
    merge_entity,
    upsert_companies,
    upsert_company_entity,
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

    plain = find_plain_entities_matching_aliases(conn)
    for entity_id, corp_code in plain:
        company_id = upsert_company_entity(conn, corp_code=corp_code)
        merge_entity(conn, source_id=entity_id, target_id=company_id)
    return len(joined), len(plain)
