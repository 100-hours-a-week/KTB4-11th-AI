import sqlalchemy as sa
from fastapi import FastAPI, HTTPException

from disclosure_retrieval.controllers import disclosures, financials, sections
from disclosure_retrieval.settings import Settings


def create_app(engine: sa.Engine, settings: Settings) -> FastAPI:
    app = FastAPI(title="disclosure-retrieval")
    app.state.engine = engine
    app.state.settings = settings
    app.include_router(disclosures.router)
    app.include_router(sections.router)
    app.include_router(financials.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
        except sa.exc.SQLAlchemyError:
            raise HTTPException(status_code=503, detail="database is unavailable") from None
        return {"status": "ok"}

    return app
