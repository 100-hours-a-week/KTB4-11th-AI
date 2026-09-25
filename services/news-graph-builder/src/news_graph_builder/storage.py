from datetime import datetime

import sqlalchemy as sa

# Mirrors infrastructure/postgres/migrations for queries only; the migrations own the schema.
metadata = sa.MetaData()

articles = sa.Table(
    "articles",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
)

clusters = sa.Table(
    "clusters",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

article_clusters = sa.Table(
    "article_clusters",
    metadata,
    sa.Column("article_id", sa.BigInteger, sa.ForeignKey("articles.id"), primary_key=True),
    sa.Column("cluster_id", sa.BigInteger, sa.ForeignKey("clusters.id"), nullable=False),
)

companies = sa.Table(
    "companies",
    metadata,
    sa.Column("corp_code", sa.Text, primary_key=True),
    sa.Column("stock_code", sa.Text, nullable=False),
    sa.Column("corp_name", sa.Text, nullable=False),
    sa.Column("corp_eng_name", sa.Text, nullable=True),
    sa.Column(
        "synced_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

company_aliases = sa.Table(
    "company_aliases",
    metadata,
    sa.Column("alias", sa.Text, primary_key=True),
    sa.Column("corp_code", sa.Text, sa.ForeignKey("companies.corp_code"), nullable=False),
)

entities = sa.Table(
    "entities",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("raw_name", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("corp_code", sa.Text, sa.ForeignKey("companies.corp_code"), nullable=True),
    sa.Index(
        "entities_corp_code_key",
        "corp_code",
        unique=True,
        postgresql_where=sa.text("corp_code IS NOT NULL"),
    ),
    sa.Index(
        "entities_name_type_key",
        "name",
        "type",
        unique=True,
        postgresql_where=sa.text("corp_code IS NULL"),
    ),
)

cluster_summaries = sa.Table(
    "cluster_summaries",
    metadata,
    sa.Column(
        "cluster_id",
        sa.BigInteger,
        sa.ForeignKey("clusters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
    sa.Column("cluster_updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "summarized_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

cluster_entities = sa.Table(
    "cluster_entities",
    metadata,
    sa.Column(
        "cluster_id",
        sa.BigInteger,
        sa.ForeignKey("clusters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), primary_key=True),
    sa.Index("cluster_entities_entity_id_idx", "entity_id"),
)

relations = sa.Table(
    "relations",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column(
        "cluster_id",
        sa.BigInteger,
        sa.ForeignKey("clusters.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("source_entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("target_entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
    sa.Index("relations_cluster_id_idx", "cluster_id"),
    sa.Index("relations_source_entity_id_idx", "source_entity_id"),
    sa.Index("relations_target_entity_id_idx", "target_entity_id"),
)


def due_clusters(conn: sa.Connection) -> list[tuple[int, datetime]]:
    query = (
        sa.select(clusters.c.id, clusters.c.updated_at)
        .outerjoin(cluster_summaries, cluster_summaries.c.cluster_id == clusters.c.id)
        .where(
            sa.or_(
                cluster_summaries.c.cluster_id.is_(None),
                cluster_summaries.c.cluster_updated_at < clusters.c.updated_at,
            )
        )
        .order_by(clusters.c.id)
    )
    return [(row.id, row.updated_at) for row in conn.execute(query)]


def cluster_articles(conn: sa.Connection, cluster_id: int) -> list[tuple[str, str]]:
    query = (
        sa.select(articles.c.title, articles.c.body)
        .join(article_clusters, article_clusters.c.article_id == articles.c.id)
        .where(article_clusters.c.cluster_id == cluster_id)
        .order_by(articles.c.published_at.desc(), articles.c.id.desc())
    )
    return [(row.title, row.body) for row in conn.execute(query)]


def has_companies(conn: sa.Connection) -> bool:
    return conn.execute(sa.select(companies.c.corp_code).limit(1)).first() is not None
