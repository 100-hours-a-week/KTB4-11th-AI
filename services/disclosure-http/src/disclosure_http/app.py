import logging
from dataclasses import asdict
from datetime import date
from typing import Annotated, Any, Literal

import sqlalchemy as sa
from disclosure_retrieval.financials import FinancialFact
from disclosure_retrieval.inference import InputBudgetError
from disclosure_retrieval.queries import report_rows
from disclosure_retrieval.search import SearchRequest, search
from disclosure_retrieval.sections import Section, read_sections
from disclosure_retrieval.settings import Settings as RetrievalSettings
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from ktb_core.clock_file import read_clock
from pydantic import BaseModel, ValidationError

from disclosure_http.filings import is_published, published_by, read_published_financials

log = logging.getLogger(__name__)
StockCode = Annotated[str, Path(pattern=r"^[0-9]{6}$")]
DetailType = Annotated[str | None, Query(pattern=r"^[A-Z][0-9]{3}$")]


class Report(BaseModel):
    receipt_id: str
    title: str
    stock_code: str
    business_year: int | None
    kind: str
    detail_type: str | None
    period_end: date | None
    published_date: date
    status: str
    index_state: str


def _cutoff(request: Request) -> date:
    try:
        return published_by(read_clock(request.app.state.clock_file))
    except (OSError, ValueError) as error:
        log.error("evaluation clock unreadable: %s", error)
        raise HTTPException(503, "evaluation clock is unavailable") from error


Cutoff = Annotated[date, Depends(_cutoff)]


def create_app(
    engine: sa.Engine, retrieval: RetrievalSettings, clock_file: str, public_url: str
) -> FastAPI:
    app = FastAPI(
        title="disclosure-http-evaluation",
        description="OpenDART filings published before the decision date: the latest reports,"
        " narrative search, sections, and financial facts.",
        servers=[{"url": public_url}],
        docs_url=None,
        redoc_url=None,
    )
    app.state.clock_file = clock_file

    @app.get(
        "/disclosures/{code}",
        operation_id="list_disclosures",
        summary="Latest periodic and major filings for one stock",
        description="Up to three latest filings published before the decision date, with the"
        " receipt_id that sections need. index_state ready means narrative search covers it.",
    )
    def list_disclosures(
        code: StockCode,
        cutoff: Cutoff,
        detail_type: DetailType = None,
        limit: Annotated[int, Query(ge=1, le=3)] = 3,
    ) -> list[Report]:
        with engine.connect() as conn:
            rows = report_rows(
                conn, code, limit, cutoff, retrieval.index_model, retrieval.dimensions, detail_type
            )
        return [Report.model_validate(row) for row in rows]

    @app.get(
        "/disclosures/{code}/search",
        operation_id="search_disclosures",
        summary="Search narrative passages in filings published before the decision date",
        description="Searches the latest three filings, or older ones too with include_history."
        " Returns evidence only; empty evidence does not mean absence of risk. For exact amounts"
        " use get_disclosure_financials.",
    )
    def search_disclosures(
        code: StockCode,
        cutoff: Cutoff,
        query: Annotated[str, Query(min_length=1, max_length=4000)],
        include_history: bool = False,
        detail_type: DetailType = None,
    ) -> dict[str, Any]:
        try:
            body = SearchRequest(
                query=query,
                include_history=include_history,
                detail_type=detail_type,
                as_of=cutoff,
            )
            result = asdict(search(engine, code, body, retrieval))
        except ValidationError as error:
            raise HTTPException(422, error.errors(include_input=False)[0]["msg"]) from None
        except InputBudgetError:
            raise HTTPException(422, "query exceeds the model input budget") from None
        except (RuntimeError, ValueError) as error:
            log.error("disclosure search failed: %s", error)
            raise HTTPException(502, "disclosure search is unavailable") from None
        for group in ("latest_evidence", "historical_evidence"):
            for evidence in result[group]:
                evidence.pop("embedding_text", None)
        return result

    @app.get(
        "/disclosures/{code}/sections",
        operation_id="get_disclosure_sections",
        summary="Read sections of one filing",
        description="Use receipt_id from list_disclosures or search evidence; filter by"
        " heading, topic or document_name. Filings published on or after the decision date"
        " are not found.",
    )
    def get_disclosure_sections(
        code: StockCode,
        cutoff: Cutoff,
        receipt_id: Annotated[str, Query(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9-]+$")],
        document_name: Annotated[str | None, Query(max_length=260)] = None,
        topic: Annotated[str | None, Query(max_length=40)] = None,
        heading: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[Section]:
        with engine.connect() as conn:
            if not is_published(conn, code, receipt_id, cutoff):
                raise HTTPException(404, "no such filing before the decision date")
            return read_sections(
                conn,
                code,
                receipt_id,
                document_name=document_name,
                topic=topic,
                heading=heading,
                limit=limit,
            )

    @app.get(
        "/disclosures/{code}/financials",
        operation_id="get_disclosure_financials",
        summary="Exact financial facts from filings published before the decision date",
        description="Give business_year or period_end. CFS is consolidated, OFS is separate."
        " Keep source_value, currency and scope; a null value is unknown, not zero.",
    )
    def get_disclosure_financials(
        code: StockCode,
        cutoff: Cutoff,
        business_year: Annotated[int | None, Query(ge=2015, le=2100)] = None,
        period_end: date | None = None,
        scope: Literal["CFS", "OFS"] | None = None,
        statement_type: Annotated[str | None, Query(max_length=20, pattern=r"^[A-Z]+$")] = None,
        account_id: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[FinancialFact]:
        if business_year is None and period_end is None:
            raise HTTPException(422, "give business_year or period_end")
        with engine.connect() as conn:
            return read_published_financials(
                conn,
                code,
                cutoff,
                period_end=period_end,
                business_year=business_year,
                scope=scope,
                statement_type=statement_type,
                account_id=account_id,
                limit=limit,
            )

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
