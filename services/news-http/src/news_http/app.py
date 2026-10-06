import sqlalchemy as sa
from fastapi import FastAPI


def create_app(engine: sa.Engine) -> FastAPI:
    app = FastAPI(title="news-http")
    app.state.engine = engine

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
