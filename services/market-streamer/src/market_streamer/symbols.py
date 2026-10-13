import sqlalchemy as sa


def load_codes(dsn: str, index_name: str) -> list[str]:
    engine = sa.create_engine(dsn)
    try:
        with engine.connect() as conn:
            codes = list(
                conn.execute(
                    sa.text(
                        "SELECT stock_code FROM corporation_indices"
                        " WHERE index_name = :index_name ORDER BY stock_code"
                    ),
                    {"index_name": index_name},
                ).scalars()
            )
    finally:
        engine.dispose()
    if not codes:
        raise RuntimeError(
            f"corporation_indices has no rows for index_name={index_name!r}; run market-syncer"
        )
    return codes
