from fastapi import APIRouter

from news_http.controllers.database import DbConnection
from news_http.controllers.pagination import Before, Limit, Page, paginate
from news_http.repositories.clusters import Cluster, find_stock_clusters

router = APIRouter()


@router.get("/stocks/{stock_code}/clusters")
def list_stock_clusters(
    stock_code: str, conn: DbConnection, before: Before, limit: Limit = 20
) -> Page[Cluster]:
    rows = find_stock_clusters(conn, stock_code, limit + 1, before)
    return paginate(rows, limit, lambda cluster: (cluster.latest_published_at, cluster.id))
