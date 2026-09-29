import sqlalchemy as sa
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from portfolio_builder.errors import ToolError
from portfolio_builder.tools.binding import bind
from portfolio_builder.tools.result import json_result


class GetNewsClusterArgs(BaseModel):
    id: int = Field(description="cluster_id")


def get_news_cluster(id: int, *, engine: sa.Engine) -> str:
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
        return json_result(
            {
                **summary,
                "articles": [dict(a) for a in articles],
                "entities": [dict(e) for e in entities],
                "relations": [dict(r) for r in relations],
            }
        )


def get_news_cluster_tool(engine: sa.Engine) -> BaseTool:
    return StructuredTool.from_function(
        bind(get_news_cluster, engine=engine),
        name="get_news_cluster",
        description=(
            "One news cluster by cluster_id: title, summary, member articles, entities and the"
            " relations extracted from it."
        ),
        args_schema=GetNewsClusterArgs,
    )
