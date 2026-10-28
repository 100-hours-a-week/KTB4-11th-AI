from collections.abc import Iterator
from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, Request


def connect(request: Request) -> Iterator[sa.Connection]:
    engine: sa.Engine = request.app.state.engine
    with engine.connect() as conn:
        yield conn


DbConnection = Annotated[sa.Connection, Depends(connect)]
