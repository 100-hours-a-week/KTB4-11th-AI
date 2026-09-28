import re
from typing import Annotated

import sqlalchemy as sa
from langchain.tools import tool
from langchain_core.tools import BaseTool
from pydantic import Field

from portfolio_builder.errors import ToolError
from portfolio_builder.tools import to_json

_TSQUERY_OPERATORS = re.compile(r"[&|!():*<>'\\\x00]")


def to_prefix_query(query: str) -> str:
    terms = (_TSQUERY_OPERATORS.sub("", term) for term in query.split())
    return " & ".join(f"{term}:*" for term in terms if term)


def news_tools(engine: sa.Engine) -> list[BaseTool]:
    @tool(
        "get_news_cluster",
        description=(
            "One news cluster by cluster_id: title, summary, member articles, entities and the"
            " relations extracted from it."
        ),
    )
    def get_news_cluster(id: Annotated[int, Field(description="cluster_id")]) -> str:
        with engine.connect() as conn:
            summary = (
                conn.execute(
                    sa.text(
                        "SELECT s.cluster_id, s.title, s.summary, c.updated_at"
                        " FROM cluster_summaries s JOIN clusters c ON c.id = s.cluster_id"
                        " WHERE s.cluster_id = :id"
                    ),
                    {"id": id},
                )
                .mappings()
                .first()
            )
            if summary is None:
                raise ToolError(f"news cluster {id} does not exist or has no summary yet")
            articles = conn.execute(
                sa.text(
                    "SELECT a.title, a.source, a.published_at"
                    " FROM article_clusters ac JOIN articles a ON a.id = ac.article_id"
                    " WHERE ac.cluster_id = :id ORDER BY a.published_at DESC"
                ),
                {"id": id},
            ).mappings()
            entities = conn.execute(
                sa.text(
                    "SELECT e.id, e.raw_name AS name, e.type, e.corp_code AS company_id"
                    " FROM cluster_entities ce JOIN entities e ON e.id = ce.entity_id"
                    " WHERE ce.cluster_id = :id ORDER BY e.id"
                ),
                {"id": id},
            ).mappings()
            relations = conn.execute(
                sa.text(
                    "SELECT s.raw_name AS source, r.type, t.raw_name AS target, r.description"
                    " FROM relations r"
                    " JOIN entities s ON s.id = r.source_entity_id"
                    " JOIN entities t ON t.id = r.target_entity_id"
                    " WHERE r.cluster_id = :id ORDER BY r.id"
                ),
                {"id": id},
            ).mappings()
            return to_json(
                {
                    **summary,
                    "articles": [dict(a) for a in articles],
                    "entities": [dict(e) for e in entities],
                    "relations": [dict(r) for r in relations],
                }
            )

    @tool(
        "search_news_cluster",
        description=(
            "Full-text search over every news cluster's title and summary (not only the briefing"
            " window). Every word must match as a prefix. Returns up to 10 clusters, best first."
        ),
    )
    def search_news_cluster(
        query: Annotated[str, Field(description="space-separated words")],
    ) -> str:
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
            return to_json([dict(r) for r in rows])

    return [get_news_cluster, search_news_cluster]
