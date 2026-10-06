import sqlalchemy as sa
from fastapi import FastAPI

from news_http.controllers import clusters


def create_app(engine: sa.Engine) -> FastAPI:
    app = FastAPI(title="news-http")
    app.state.engine = engine
    app.include_router(clusters.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
