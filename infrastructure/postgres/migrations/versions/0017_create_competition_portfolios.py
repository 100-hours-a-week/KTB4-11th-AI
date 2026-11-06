"""create competition participant, portfolio link, and job tables

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "competition_participants",
        sa.Column("participant_id", sa.BigInteger, primary_key=True),
        sa.Column("competition_id", sa.BigInteger, nullable=False),
        sa.Column("user_id", sa.BigInteger, nullable=False),
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
        sa.Column("additional_request", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "competition_participants_competition_id_idx",
        "competition_participants",
        ["competition_id"],
    )
    op.create_table(
        "competition_portfolios",
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
    )
    op.create_index(
        "competition_portfolios_portfolio_id_idx",
        "competition_portfolios",
        ["portfolio_id"],
    )
    op.create_table(
        "competition_jobs",
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
    )
    op.create_check_constraint(
        "competition_jobs_status_check",
        "competition_jobs",
        "status IN ('running', 'succeeded', 'failed')",
    )
    op.create_index(
        "competition_jobs_participant_id_idx",
        "competition_jobs",
        ["participant_id"],
    )
    op.add_column(
        "portfolios", sa.Column("acknowledged_constraints", postgresql.JSONB, nullable=True)
    )


def downgrade() -> None:
    op.drop_column("portfolios", "acknowledged_constraints")
    op.drop_index("competition_jobs_participant_id_idx", table_name="competition_jobs")
    op.drop_constraint("competition_jobs_status_check", "competition_jobs", type_="check")
    op.drop_table("competition_jobs")
    op.drop_index("competition_portfolios_portfolio_id_idx", table_name="competition_portfolios")
    op.drop_table("competition_portfolios")
    op.drop_index(
        "competition_participants_competition_id_idx", table_name="competition_participants"
    )
    op.drop_table("competition_participants")
