from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None


def encode_cursor(at: datetime, id: int) -> str:
    return urlsafe_b64encode(f"{at.isoformat()}|{id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        at, id = urlsafe_b64decode(cursor).decode().split("|")
        decoded = datetime.fromisoformat(at), int(id)
    except ValueError as error:
        raise HTTPException(422, "invalid cursor") from error
    if decoded[0].tzinfo is None:
        raise HTTPException(422, "invalid cursor")
    return decoded


def paginate[T](rows: list[T], limit: int, key: Callable[[T], tuple[datetime, int]]) -> Page[T]:
    if len(rows) <= limit:
        return Page(items=rows, next_cursor=None)
    return Page(items=rows[:limit], next_cursor=encode_cursor(*key(rows[limit - 1])))


def read_cursor(cursor: str | None = None) -> tuple[datetime, int] | None:
    return None if cursor is None else decode_cursor(cursor)


Limit = Annotated[int, Query(ge=1, le=100)]
Before = Annotated[tuple[datetime, int] | None, Depends(read_cursor)]
