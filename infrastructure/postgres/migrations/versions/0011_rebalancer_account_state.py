import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    state = op.create_table(
        "rebalancer_account_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Text),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", sa.Text),
        sa.Column("payload", sa.JSON),
        sa.Column("usable", sa.Boolean, nullable=False),
        sa.CheckConstraint("id = 1", name="rebalancer_account_state_singleton"),
    )
    op.bulk_insert(state, [{"id": 1, "usable": False}])
    op.create_table(
        "rebalancer_snapshot_events",
        sa.Column("event_id", sa.Text, primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("rebalancer_snapshot_events")
    op.drop_table("rebalancer_account_state")
