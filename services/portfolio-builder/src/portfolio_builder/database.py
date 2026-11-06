import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()

corporations = sa.Table(
    "corporations",
    metadata,
    sa.Column("stock_code", sa.Text, primary_key=True),
    sa.Column("corp_code", sa.Text, nullable=False, unique=True),
)

portfolios = sa.Table(
    "portfolios",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("cash_weight", sa.Double, nullable=False),
    sa.Column("commentary", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("trace", postgresql.JSONB, nullable=True),
    sa.Column(
        "status",
        sa.Text,
        nullable=False,
        server_default=sa.text("'explanation_pending'"),
    ),
    sa.Column("acknowledged_constraints", postgresql.JSONB, nullable=True),
    sa.Column("explanation_attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Index("portfolios_created_at_idx", "created_at"),
)

portfolio_holdings = sa.Table(
    "portfolio_holdings",
    metadata,
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("company_id", sa.Text, sa.ForeignKey("corporations.corp_code"), primary_key=True),
    sa.Column("weight", sa.Double, nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column(
        "cited_cluster_ids",
        postgresql.ARRAY(sa.BigInteger),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
)

portfolio_exits = sa.Table(
    "portfolio_exits",
    metadata,
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("company_id", sa.Text, sa.ForeignKey("corporations.corp_code"), primary_key=True),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column(
        "cited_cluster_ids",
        postgresql.ARRAY(sa.BigInteger),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
)

portfolio_reasons = sa.Table(
    "portfolio_reasons",
    metadata,
    sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
        unique=True,
    ),
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("company_id", sa.Text, sa.ForeignKey("corporations.corp_code"), primary_key=True),
    sa.Column("side", sa.Text, primary_key=True),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("reasonings", postgresql.JSONB, nullable=False),
    sa.CheckConstraint("side IN ('buy', 'sell')", name="portfolio_reasons_side_check"),
)

competition_participants = sa.Table(
    "competition_participants",
    metadata,
    sa.Column("participant_id", sa.BigInteger, primary_key=True),
    sa.Column("competition_id", sa.BigInteger, nullable=False),
    sa.Column("user_id", sa.BigInteger, nullable=False),
    sa.Column("agent_id", sa.BigInteger, nullable=True),
    sa.Column("risk_profile", sa.Text, nullable=False),
    sa.Column(
        "interested_theme_codes",
        postgresql.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
    sa.Column(
        "excluded_theme_codes",
        postgresql.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
    sa.Column(
        "interested_theme_names",
        postgresql.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
    sa.Column(
        "excluded_theme_names",
        postgresql.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
    sa.Column("additional_request", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("canonical_form", postgresql.JSONB, nullable=True),
    sa.Column("canonical_version", sa.Text, nullable=True),
    sa.Column("classified", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.CheckConstraint(
        "risk_profile IN ('성장형', '성장추구형', '위험중립형', '안정추구형', '안정형')",
        name="competition_participants_risk_profile_check",
    ),
    sa.Index("competition_participants_competition_id_idx", "competition_id"),
)

competition_portfolios = sa.Table(
    "competition_portfolios",
    metadata,
    sa.Column(
        "participant_id",
        sa.BigInteger,
        sa.ForeignKey("competition_participants.participant_id"),
        primary_key=True,
    ),
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Index("competition_portfolios_portfolio_id_idx", "portfolio_id"),
)

competition_jobs = sa.Table(
    "competition_jobs",
    metadata,
    sa.Column("generation_key", sa.Text, primary_key=True),
    sa.Column(
        "participant_id",
        sa.BigInteger,
        sa.ForeignKey("competition_participants.participant_id"),
        nullable=False,
    ),
    sa.Column(
        "previous_portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column(
        "portfolio_id",
        sa.BigInteger,
        sa.ForeignKey("portfolios.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("error", sa.Text, nullable=True),
    sa.Column(
        "started_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "status IN ('running', 'succeeded', 'failed')",
        name="competition_jobs_status_check",
    ),
    sa.Index("competition_jobs_participant_id_idx", "participant_id"),
)

competition_notifications = sa.Table(
    "competition_notifications",
    metadata,
    sa.Column(
        "participant_id",
        sa.BigInteger,
        sa.ForeignKey("competition_participants.participant_id"),
        primary_key=True,
    ),
    sa.Column(
        "generation_key",
        sa.Text,
        sa.ForeignKey("competition_jobs.generation_key"),
        primary_key=True,
    ),
    sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(text: str) -> str:
    return f"%{_escape_like(text)}%"


def like_group_prefix(text: str) -> str:
    return f"{_escape_like(text)}\\_%"


def like_group_suffix(text: str) -> str:
    return f"%\\_{_escape_like(text)}"
