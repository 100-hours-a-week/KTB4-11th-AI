from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from news_http.controllers.database import DbConnection
from news_http.controllers.pagination import BIGINT_MAX, Before, Limit, Page, paginate
from news_http.controllers.responses import ClusterDetail, ClusterSearchResult
from news_http.repositories.clusters import (
    Cluster,
    find_stock_clusters,
    get_cluster_detail,
    search_clusters,
)

router = APIRouter()


@router.get("/clusters/search", response_model=list[ClusterSearchResult])
def search_cluster_route(q: Annotated[str, Query()], conn: DbConnection) -> list[dict[str, object]]:
    if not q.strip():
        raise HTTPException(422, "q must contain comma-separated words")
    terms = [term.strip() for term in q.split(",")]
    if any(not term or not term.isalnum() for term in terms):
        raise HTTPException(422, "q must contain comma-separated words")
    return search_clusters(conn, terms)


@router.get("/clusters/{cluster_id}", response_model=ClusterDetail)
def get_cluster_route(
    cluster_id: Annotated[int, Path(ge=1, le=BIGINT_MAX)], conn: DbConnection
) -> dict[str, object]:
    detail = get_cluster_detail(conn, cluster_id)
    if detail is None:
        raise HTTPException(404, "cluster not found")
    return detail


@router.get("/stocks/{stock_code}/clusters")
def list_stock_clusters(
    stock_code: str, conn: DbConnection, before: Before, limit: Limit = 20
) -> Page[Cluster]:
    rows = find_stock_clusters(conn, stock_code, limit + 1, before)
    return paginate(rows, limit, lambda cluster: (cluster.latest_published_at, cluster.id))
