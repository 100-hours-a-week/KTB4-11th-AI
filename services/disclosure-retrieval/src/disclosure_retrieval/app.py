import sqlalchemy as sa
from fastapi import FastAPI

from disclosure_retrieval.routes import router
from disclosure_retrieval.settings import Settings


def create_app(engine: sa.Engine, settings: Settings) -> FastAPI:
    app = FastAPI(title="disclosure-retrieval")
    app.state.engine = engine
    app.state.settings = settings
    app.include_router(router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
