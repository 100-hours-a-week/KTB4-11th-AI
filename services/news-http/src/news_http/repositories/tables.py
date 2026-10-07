import sqlalchemy as sa

metadata = sa.MetaData()

articles = sa.Table(
    "articles",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
)

clusters = sa.Table(
    "clusters",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
)

article_clusters = sa.Table(
    "article_clusters",
    metadata,
    sa.Column("article_id", sa.BigInteger, sa.ForeignKey("articles.id"), primary_key=True),
    sa.Column("cluster_id", sa.BigInteger, sa.ForeignKey("clusters.id"), nullable=False),
)

cluster_summaries = sa.Table(
    "cluster_summaries",
    metadata,
    sa.Column("cluster_id", sa.BigInteger, sa.ForeignKey("clusters.id"), primary_key=True),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("summary", sa.Text, nullable=False),
)

entities = sa.Table(
    "entities",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("stock_code", sa.Text, nullable=True),
)

cluster_entities = sa.Table(
    "cluster_entities",
    metadata,
    sa.Column("cluster_id", sa.BigInteger, sa.ForeignKey("clusters.id"), primary_key=True),
    sa.Column("entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), primary_key=True),
)
