import sqlalchemy as sa


def postgres_stock_metadata(engine: sa.Engine, codes: list[str]) -> dict[str, dict[str, str | None]]:
    if not codes:
        return {}
    statement = sa.text(
        "SELECT stock_code, name, sector_name FROM corporations WHERE stock_code IN :codes"
    ).bindparams(sa.bindparam("codes", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(statement, {"codes": codes}).mappings()
        return {
            row["stock_code"]: {"stock_name": row["name"], "sector_name": row["sector_name"]}
            for row in rows
        }
