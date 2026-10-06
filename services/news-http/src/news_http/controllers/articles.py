from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from news_http.controllers.database import DbConnection
from news_http.controllers.pagination import BIGINT_MAX, Before, Limit, Page, paginate
from news_http.repositories.articles import Article, find_cluster_articles
from news_http.repositories.clusters import cluster_exists

router = APIRouter()


@router.get("/clusters/{cluster_id}/articles")
def list_cluster_articles(
    cluster_id: Annotated[int, Path(ge=1, le=BIGINT_MAX)],
    conn: DbConnection,
    before: Before,
    limit: Limit = 20,
) -> Page[Article]:
    rows = find_cluster_articles(conn, cluster_id, limit + 1, before)
    if not rows and not cluster_exists(conn, cluster_id):
        raise HTTPException(404, "cluster not found")
    return paginate(rows, limit, lambda article: (article.published_at, article.id))
