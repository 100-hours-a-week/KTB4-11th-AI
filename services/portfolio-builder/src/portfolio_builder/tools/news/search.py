import re

import sqlalchemy as sa
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.errors import ToolError
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.result import json_result

_TSQUERY_OPERATORS = re.compile(r"[&|!():*<>'\\\x00]")


class SearchNewsClusterArgs(BaseModel):
    query: str = Field(description="space-separated words")


def to_prefix_query(query: str) -> str:
    terms = (_TSQUERY_OPERATORS.sub("", term) for term in query.split())
    return " & ".join(f"{term}:*" for term in terms if term)


def search_news_cluster(query: str, *, engine: sa.Engine) -> str:
    tsquery = to_prefix_query(query)
    if not tsquery:
        raise ToolError("query has no searchable words")
    # The to_tsvector expression must match cluster_summaries_fts_idx exactly.
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT s.cluster_id, s.title, left(s.summary, 200) AS excerpt,"
                " ts_rank(to_tsvector('simple', s.title || ' ' || s.summary), q) AS rank"
                " FROM cluster_summaries s, to_tsquery('simple', :q) AS q"
                " WHERE to_tsvector('simple', s.title || ' ' || s.summary) @@ q"
                " ORDER BY rank DESC, s.cluster_id DESC LIMIT 10"
            ),
            {"q": tsquery},
        ).mappings()
        return json_result([dict(r) for r in rows])


def search_news_cluster_tool(engine: sa.Engine) -> BaseTool:
    return StructuredTool.from_function(
        bind(search_news_cluster, engine=engine),
        name="search_news_cluster",
        description=(
            "Full-text search over every news cluster's title and summary (not only the briefing"
            " window). Every word must match as a prefix. Returns up to 10 clusters, best first."
        ),
        args_schema=SearchNewsClusterArgs,
    )
