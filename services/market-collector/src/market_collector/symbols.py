import sqlalchemy as sa
from ktb_core.logging import get_logger

__all__ = ["EmptyIndexError", "load_symbols"]

log = get_logger(__name__)


class EmptyIndexError(RuntimeError):
    pass


def load_symbols(dsn: str, index_name: str) -> list[str]:
    engine = sa.create_engine(dsn)
    try:
        with engine.connect() as conn:
            symbols = list(
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
    if not symbols:
        raise EmptyIndexError(
            f"corporation_indices has no rows for index_name={index_name!r}; run market-syncer"
        )
    log.info("symbols_loaded", count=len(symbols), index_name=index_name)
    return symbols
