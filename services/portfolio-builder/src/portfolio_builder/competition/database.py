import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from portfolio_builder.database import metadata

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

competition_attempts = sa.Table(
    "competition_attempts",
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
    sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
)
