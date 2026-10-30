import sqlalchemy as sa
from disclosure_retrieval.settings import Settings
from langchain_core.tools import BaseTool, StructuredTool

from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.disclosure.get import (
    GetFinancialsArgs,
    GetSectionsArgs,
    get_disclosure_financials,
    get_disclosure_sections,
)
from portfolio_builder.tools.disclosure.search import SearchDisclosuresArgs, search_disclosures


def disclosure_tools(engine: sa.Engine, settings: Settings) -> list[BaseTool]:
    return [
        StructuredTool.from_function(
            bind(search_disclosures, engine=engine, settings=settings),
            name="search_disclosures",
            description=(
                "Search narrative passages in the latest three OpenDART filings. "
                "Use detail_type to select a filing type, include_history for older filings, "
                "and as_of to limit publication dates. "
                "Returns evidence only; empty evidence does not mean absence of risk. "
                "For exact financial amounts use get_disclosure_financials; "
                "for a known report section use get_disclosure_sections."
            ),
            args_schema=SearchDisclosuresArgs,
        ),
        StructuredTool.from_function(
            bind(get_disclosure_sections, engine=engine),
            name="get_disclosure_sections",
            description=(
                "Read sections of an identified filing directly from the database, without GPU. "
                "Use receipt_id from search evidence; filter by heading, topic or document_name."
            ),
            args_schema=GetSectionsArgs,
        ),
        StructuredTool.from_function(
            bind(get_disclosure_financials, engine=engine),
            name="get_disclosure_financials",
            description=(
                "Read exact financial facts directly from the database, without GPU. "
                "Specify business_year or period_end; CFS is consolidated, OFS is separate. "
                "Keep source_value, currency and scope; a null amount is unknown, not zero."
            ),
            args_schema=GetFinancialsArgs,
        ),
    ]
