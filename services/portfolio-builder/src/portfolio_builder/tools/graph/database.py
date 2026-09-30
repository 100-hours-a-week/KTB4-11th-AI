from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa

from portfolio_builder.errors import GraphTimeout

QUERY_CANCELED = "57014"


@contextmanager
def graph_transaction(engine: sa.Engine) -> Iterator[sa.Connection]:
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("SET LOCAL statement_timeout = '10s'"))
            yield conn
    except sa.exc.OperationalError as error:
        if getattr(error.orig, "sqlstate", None) == QUERY_CANCELED:
            raise GraphTimeout(
                "graph query timed out; use a smaller depth or a more specific name"
            ) from error
        raise
