from collections.abc import Collection, Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from market_syncer.common import normalize
from market_syncer.dart import DartCorporation
from market_syncer.database import corporation_aliases, corporation_indices, corporations

__all__ = ["has_corporations", "replace_index", "sync_corporations"]


def has_corporations(conn: sa.Connection) -> bool:
    return conn.execute(sa.select(corporations.c.stock_code).limit(1)).first() is not None


def sync_corporations(
    conn: sa.Connection, kospi: Sequence[tuple[str, str]], dart: Sequence[DartCorporation]
) -> int:
    by_stock_code = {company.stock_code: company for company in dart}
    joined = {code: (by_stock_code[code], name) for code, name in kospi if code in by_stock_code}
    if not joined:
        raise ValueError(f"none of {len(kospi)} KOSPI codes matched a DART stock_code")

    upsert_corporations(conn, [company for company, _ in joined.values()])
    insert_aliases(
        conn,
        [
            (alias, company.stock_code)
            for company, kiwoom_name in joined.values()
            for alias in dict.fromkeys(
                normalize(name)
                for name in (company.corp_name, kiwoom_name, company.corp_eng_name or "")
            )
            if alias
        ],
    )
    return len(joined)


def upsert_corporations(conn: sa.Connection, rows: Sequence[DartCorporation]) -> None:
    statement = insert(corporations).values(
        [
            {
                "stock_code": company.stock_code,
                "corp_code": company.corp_code,
                "name": company.corp_name,
                "eng_name": company.corp_eng_name,
            }
            for company in rows
        ]
    )
    conn.execute(
        statement.on_conflict_do_update(
            index_elements=[corporations.c.stock_code],
            set_={
                "corp_code": statement.excluded.corp_code,
                "name": statement.excluded.name,
                "eng_name": statement.excluded.eng_name,
                "synced_at": sa.func.now(),
            },
        )
    )


def insert_aliases(conn: sa.Connection, aliases: Sequence[tuple[str, str]]) -> None:
    conn.execute(
        insert(corporation_aliases)
        .values([{"alias": alias, "stock_code": stock_code} for alias, stock_code in aliases])
        .on_conflict_do_nothing()
    )


def replace_index(
    conn: sa.Connection, index_name: str, stock_codes: Collection[str]
) -> tuple[int, int]:
    if not stock_codes:
        raise ValueError(f"Kiwoom returned no constituents for {index_name}")

    known = set(
        conn.execute(
            sa.select(corporations.c.stock_code).where(
                corporations.c.stock_code.in_(sorted(stock_codes))
            )
        ).scalars()
    )
    if not known:
        raise ValueError(f"no {index_name} constituent is a KOSPI corporation")

    conn.execute(
        sa.delete(corporation_indices).where(corporation_indices.c.index_name == index_name)
    )
    conn.execute(
        insert(corporation_indices),
        [{"stock_code": code, "index_name": index_name} for code in sorted(known)],
    )
    return len(known), len(set(stock_codes) - known)
