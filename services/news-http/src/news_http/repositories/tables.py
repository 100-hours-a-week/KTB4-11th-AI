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
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
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
    sa.Column("raw_name", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("stock_code", sa.Text, nullable=True),
)

corporations = sa.Table(
    "corporations",
    metadata,
    sa.Column("stock_code", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("corp_code", sa.Text, nullable=False),
)

themes = sa.Table(
    "themes",
    metadata,
    sa.Column("theme_code", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
)

theme_companies = sa.Table(
    "theme_companies",
    metadata,
    sa.Column("theme_code", sa.Text, sa.ForeignKey("themes.theme_code"), primary_key=True),
    sa.Column("stock_code", sa.Text, sa.ForeignKey("corporations.stock_code"), primary_key=True),
    sa.Column("is_major", sa.Boolean, nullable=False),
)

cluster_entities = sa.Table(
    "cluster_entities",
    metadata,
    sa.Column("cluster_id", sa.BigInteger, sa.ForeignKey("clusters.id"), primary_key=True),
    sa.Column("entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), primary_key=True),
)

relations = sa.Table(
    "relations",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("cluster_id", sa.BigInteger, sa.ForeignKey("clusters.id"), nullable=False),
    sa.Column("source_entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("target_entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False),
)
