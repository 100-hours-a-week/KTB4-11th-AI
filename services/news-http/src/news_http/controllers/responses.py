from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ClusterSearchResult(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 42,
                    "title": "Semiconductor demand grows",
                    "excerpt": "Chip demand increased.",
                    "rank": 0.42,
                }
            ]
        }
    )

    id: int
    title: str
    excerpt: str
    rank: float


class ClusterArticle(BaseModel):
    title: str
    source: str
    published_at: datetime


class ClusterEntity(BaseModel):
    id: int
    name: str
    type: str
    company_id: str | None


class ClusterRelation(BaseModel):
    source: str
    type: str
    target: str
    description: str


class ClusterDetail(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 42,
                    "title": "Semiconductor demand grows",
                    "summary": "Chip demand increased.",
                    "updated_at": "2026-10-07T09:00:00Z",
                    "articles": [
                        {
                            "title": "HBM demand rises",
                            "source": "Example News",
                            "published_at": "2026-10-07T08:30:00Z",
                        }
                    ],
                    "entities": [
                        {
                            "id": 12,
                            "name": "Samsung Electronics",
                            "type": "company",
                            "company_id": "005930",
                        },
                        {"id": 13, "name": "HBM", "type": "concept", "company_id": None},
                    ],
                    "relations": [
                        {
                            "source": "Samsung Electronics",
                            "type": "invests_in",
                            "target": "HBM",
                            "description": "Expands HBM production.",
                        }
                    ],
                }
            ]
        }
    )

    id: int
    title: str
    summary: str
    updated_at: datetime
    articles: list[ClusterArticle]
    entities: list[ClusterEntity]
    relations: list[ClusterRelation]


class RecentNewsCluster(BaseModel):
    id: int
    title: str
    summary: str
    updated_at: datetime


class RecentNewsCompany(BaseModel):
    company_id: str
    name: str
    stock_code: str
    cluster_ids: list[int]
    themes: list[str]


class RecentNews(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "clusters": [
                        {
                            "id": 42,
                            "title": "Semiconductor demand grows",
                            "summary": "Chip demand increased.",
                            "updated_at": "2026-10-07T09:00:00Z",
                        }
                    ],
                    "companies": [
                        {
                            "company_id": "00126380",
                            "name": "Samsung Electronics",
                            "stock_code": "005930",
                            "cluster_ids": [42],
                            "themes": ["Semiconductors (main)"],
                        }
                    ],
                    "theme_count": 1,
                }
            ]
        }
    )

    clusters: list[RecentNewsCluster]
    companies: list[RecentNewsCompany]
    theme_count: int


class GraphNode(BaseModel):
    id: int
    hop: int
    name: str
    type: str
    company_id: str | None


class GraphEdge(BaseModel):
    id: int
    source: str
    type: str
    target: str
    description: str
    cluster_id: int
    hop: int


class GraphNeighborhood(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "nodes": [
                        {
                            "id": 12,
                            "hop": 0,
                            "name": "Samsung Electronics",
                            "type": "company",
                            "company_id": "005930",
                        },
                        {"id": 13, "hop": 1, "name": "HBM", "type": "concept", "company_id": None},
                    ],
                    "edges": [
                        {
                            "id": 99,
                            "source": "Samsung Electronics",
                            "type": "invests_in",
                            "target": "HBM",
                            "description": "Expands HBM production.",
                            "cluster_id": 42,
                            "hop": 0,
                        }
                    ],
                    "truncated": False,
                }
            ]
        }
    )

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool


class GraphPathStep(BaseModel):
    from_: str = Field(alias="from")
    to: str
    type: str
    direction: str
    description: str
    cluster_id: int


class GraphPath(BaseModel):
    length: int
    steps: list[GraphPathStep]


class GraphPaths(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "paths": [
                        {
                            "length": 1,
                            "steps": [
                                {
                                    "from": "Samsung Electronics",
                                    "to": "HBM",
                                    "type": "invests_in",
                                    "direction": "forward",
                                    "description": "Expands HBM production.",
                                    "cluster_id": 42,
                                }
                            ],
                        }
                    ],
                    "truncated": False,
                }
            ]
        }
    )

    paths: list[GraphPath]
    truncated: bool
