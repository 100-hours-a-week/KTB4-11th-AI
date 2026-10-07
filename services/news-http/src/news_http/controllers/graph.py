from collections.abc import Callable
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query

from news_http.controllers.database import DbConnection
from news_http.repositories.graph import (
    TIMEOUT_MESSAGE,
    MissingEntity,
    get_neighborhood,
    get_paths,
)

router = APIRouter()


def _handle_query(operation: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return operation()
    except MissingEntity as error:
        raise HTTPException(
            404,
            {"message": error.message, "candidates": error.candidates},
        ) from error
    except sa.exc.OperationalError as error:
        if getattr(error.orig, "sqlstate", None) == "57014":
            raise HTTPException(
                504,
                TIMEOUT_MESSAGE,
            ) from error
        raise


@router.get("/graph/neighborhood")
def neighborhood_route(
    name: Annotated[str, Query(min_length=1)],
    conn: DbConnection,
    depth: Annotated[int, Query(ge=1, le=3)] = 2,
) -> dict[str, object]:
    if not name.strip():
        raise HTTPException(422, "name must not be empty")
    return _handle_query(lambda: get_neighborhood(conn, name, depth))


@router.get("/graph/paths")
def paths_route(
    from_name: Annotated[str, Query(min_length=1)],
    to_name: Annotated[str, Query(min_length=1)],
    conn: DbConnection,
    max_depth: Annotated[int, Query(ge=1, le=6)] = 4,
) -> dict[str, object]:
    if not from_name.strip() or not to_name.strip():
        raise HTTPException(422, "entity names must not be empty")
    return _handle_query(lambda: get_paths(conn, from_name, to_name, max_depth))
