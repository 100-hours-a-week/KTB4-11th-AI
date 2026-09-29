import sqlalchemy as sa

metadata = sa.MetaData()

corporations = sa.Table(
    "corporations",
    metadata,
    sa.Column("stock_code", sa.Text, primary_key=True),
    sa.Column("corp_code", sa.Text, nullable=False, unique=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("market", sa.Text, nullable=False, server_default="KOSPI"),
    sa.Column("eng_name", sa.Text, nullable=True),
    sa.Column(
        "synced_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

corporation_aliases = sa.Table(
    "corporation_aliases",
    metadata,
    sa.Column("alias", sa.Text, primary_key=True),
    sa.Column("stock_code", sa.Text, sa.ForeignKey("corporations.stock_code"), nullable=False),
)

corporation_indices = sa.Table(
    "corporation_indices",
    metadata,
    sa.Column("stock_code", sa.Text, sa.ForeignKey("corporations.stock_code"), primary_key=True),
    sa.Column("index_name", sa.Text, primary_key=True),
)

themes = sa.Table(
    "themes",
    metadata,
    sa.Column("theme_code", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "synced_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

theme_companies = sa.Table(
    "theme_companies",
    metadata,
    sa.Column(
        "theme_code",
        sa.Text,
        sa.ForeignKey("themes.theme_code", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "stock_code",
        sa.Text,
        sa.ForeignKey("corporations.stock_code", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("is_main", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Index("theme_companies_stock_code_idx", "stock_code"),
)
