from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict

from market_http.controllers.dependencies import CODE, QuestDBConf
from market_http.repositories.bars import Bar, Timeframe, find_bars

router = APIRouter()


class Page(BaseModel):
    items: list[Bar]
    next_cursor: str | None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "timestamp": "2026-10-15T09:01:00+09:00",
                            "open": 82000.0,
                            "high": 82300.0,
                            "low": 81900.0,
                            "close": 82100.0,
                            "volume": 15000,
                        }
                    ],
                    "next_cursor": None,
                }
            ]
        }
    )


def _cursor(cursor: str | None = None) -> datetime | None:
    if cursor is None:
        return None
    try:
        timestamp = datetime.fromisoformat(urlsafe_b64decode(cursor).decode())
    except ValueError as error:
        raise HTTPException(422, "invalid cursor") from error
    if timestamp.tzinfo is None:
        raise HTTPException(422, "invalid cursor")
    return timestamp


@router.get("/bars/{code}")
def list_bars(
    code: Annotated[str, Path(pattern=CODE)],
    timeframe: Timeframe,
    conf: QuestDBConf,
    before: Annotated[datetime | None, Depends(_cursor)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> Page:
    rows = find_bars(conf, code, timeframe, limit + 1, before)
    if len(rows) <= limit:
        return Page(items=rows, next_cursor=None)
    cursor = urlsafe_b64encode(rows[limit - 1].timestamp.isoformat().encode()).decode()
    return Page(items=rows[:limit], next_cursor=cursor)
