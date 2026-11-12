import logging
from datetime import datetime, timedelta
from typing import Annotated, Literal

import sqlalchemy as sa
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from ktb_core.clock_file import read_clock
from ktb_core.logging import setup_logging

from news_http.controllers.database import DbConnection
from news_http.controllers.pagination import Before, Page, paginate
from news_http.repositories.articles import PublishedArticle, find_published_articles
from news_http.settings import EvaluationSettings

log = logging.getLogger(__name__)


def _as_of(request: Request) -> datetime:
    try:
        return read_clock(request.app.state.clock_file)
    except (OSError, ValueError) as error:
        log.error("evaluation clock unreadable: %s", error)
        raise HTTPException(503, "evaluation clock is unavailable") from error


AsOf = Annotated[datetime, Depends(_as_of)]


def create_evaluation_app(
    engine: sa.Engine,
    clock_file: str,
    availability: Literal["published", "fetched"],
    public_url: str,
) -> FastAPI:
    app = FastAPI(
        title="news-http-evaluation",
        description="Raw articles published at or before the evaluation clock.",
        servers=[{"url": public_url}],
        docs_url=None,
        redoc_url=None,
    )
    app.state.engine = engine
    app.state.clock_file = clock_file

    @app.get(
        "/news/articles",
        operation_id="search_news",
        summary="Raw news articles published before the decision time, newest first",
        description="Articles published within the last `days` days before the decision time."
        " `q` filters title and body, for example by company or theme name. Pass next_cursor"
        " as cursor for older articles.",
    )
    def list_articles(
        conn: DbConnection,
        as_of: AsOf,
        before: Before,
        days: Annotated[int, Query(ge=1, le=90)] = 7,
        q: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> Page[PublishedArticle]:
        rows = find_published_articles(
            conn,
            as_of - timedelta(days=days),
            as_of,
            fetched_by_until=availability == "fetched",
            query=(q or "").strip() or None,
            limit=limit + 1,
            before=before,
        )
        return paginate(rows, limit, lambda article: (article.published_at, article.id))

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    settings = EvaluationSettings()
    setup_logging(settings.log_level, service_name="news-http-evaluation")
    engine = sa.create_engine(settings.postgres_dsn, pool_pre_ping=True)
    app = create_evaluation_app(
        engine,
        settings.evaluation_clock_file,
        settings.evaluation_availability,
        settings.evaluation_public_url,
    )
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
    finally:
        engine.dispose()
