from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import BaseModel

from disclosure_retrieval.controllers.database import DbConnection
from disclosure_retrieval.repositories.sections import Section, read_sections

router = APIRouter()
StockCode = Annotated[str, Path(pattern=r"^\d{6}$")]


class SectionList(BaseModel):
    sections: list[Section]


@router.get("/stocks/{stock_code}/disclosures/sections")
def sections(
    stock_code: StockCode,
    conn: DbConnection,
    receipt_id: Annotated[str, Query(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9-]+$")],
    document_name: Annotated[str | None, Query(max_length=260)] = None,
    topic: Annotated[str | None, Query(max_length=40)] = None,
    heading: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SectionList:
    """Read identified parts of one filing. No embedding server is involved."""
    return SectionList(
        sections=read_sections(
            conn,
            stock_code,
            receipt_id,
            document_name=document_name,
            topic=topic,
            heading=heading,
            limit=limit,
        )
    )
