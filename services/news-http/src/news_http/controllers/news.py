from typing import Annotated

from fastapi import APIRouter, Query

from news_http.controllers.database import DbConnection
from news_http.controllers.responses import RecentNews
from news_http.repositories.news import get_recent_news

router = APIRouter()


@router.get("/news/recent", response_model=RecentNews)
def recent_news(conn: DbConnection, days: Annotated[int, Query(gt=0)] = 7) -> dict[str, object]:
    return get_recent_news(conn, days)
