import logging
from datetime import UTC, datetime, time
from typing import Annotated, Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from ktb_core.clock_file import read_clock
from ktb_core.krx import last_closed_date
from ktb_core.logging import setup_logging
from ktb_telemetry.tracing import tracing

from market_http.controllers.bars import Code, Cursor, Limit, Page, page
from market_http.controllers.dependencies import QuestDBConf
from market_http.repositories.bars import find_bars
from market_http.settings import EvaluationSettings

log = logging.getLogger(__name__)


def _until(request: Request) -> datetime | None:
    clock_file: str | None = request.app.state.clock_file
    if clock_file is None:
        return None
    try:
        as_of = read_clock(clock_file)
    except (OSError, ValueError) as error:
        log.error("evaluation clock unreadable: %s", error)
        raise HTTPException(503, "evaluation clock is unavailable") from error
    return datetime.combine(last_closed_date(as_of), time(), tzinfo=UTC)


Until = Annotated[datetime | None, Depends(_until)]


def create_evaluation_app(
    questdb_conf: str, clock_file: str | None, public_url: str | None = None
) -> FastAPI:
    app = FastAPI(
        title="market-http-evaluation",
        description="Daily bars that end at the last market close at or before the clock."
        if clock_file
        else "Daily bars for the evaluation harness only.",
        servers=[{"url": public_url}] if public_url else None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.questdb_conf = questdb_conf
    app.state.clock_file = clock_file

    @app.get(
        "/bars/{code}",
        operation_id="get_daily_bars",
        summary="Daily OHLCV bars for one stock, newest first",
        description="Only sessions whose 15:30 KST close is at or before the decision time are"
        " returned. Pass next_cursor as cursor for older bars.",
    )
    def list_daily_bars(
        code: Code,
        timeframe: Literal["1d"],
        conf: QuestDBConf,
        until: Until,
        before: Cursor,
        limit: Limit = 300,
    ) -> Page:
        return page(find_bars(conf, code, timeframe, limit + 1, before, until), limit)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    settings = EvaluationSettings()
    setup_logging(settings.log_level, service_name="market-http-evaluation")
    app = create_evaluation_app(
        settings.questdb_conf, settings.evaluation_clock_file, settings.evaluation_public_url
    )
    with tracing("market-http-evaluation"):
        uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)
