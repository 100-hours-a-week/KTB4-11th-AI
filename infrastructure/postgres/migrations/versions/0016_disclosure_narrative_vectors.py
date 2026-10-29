import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("disclosure_indexes_dimensions_check", "disclosure_indexes")
    op.create_check_constraint(
        "disclosure_indexes_dimensions_check", "disclosure_indexes", "dimensions IN (1024, 2000)"
    )
    op.add_column(
        "disclosure_indexes",
        sa.Column("identity", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.create_check_constraint(
        "disclosure_indexes_identity_check",
        "disclosure_indexes",
        "dimensions != 1024 OR (identity ? 'adapter_sha256' AND "
        "identity->>'dimensions' = '1024' AND identity->>'max_tokens' = '16384')",
    )
    op.create_unique_constraint(
        "disclosure_indexes_id_dimensions_key", "disclosure_indexes", ["id", "dimensions"]
    )
    op.drop_index("disclosure_indexes_active_idx", table_name="disclosure_indexes")
    op.create_index(
        "disclosure_indexes_active_idx",
        "disclosure_indexes",
        ["document_id", "dimensions"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "disclosure_narrative_chunks",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("index_id", sa.BigInteger, nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=False, server_default="1024"),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("section", sa.Text, nullable=False),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("document_name", sa.Text, nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=False),
        sa.Column("end_offset", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("context", sa.Text, nullable=False, server_default=""),
        sa.Column("embedding_text", sa.Text, nullable=False),
        sa.Column("embedding", HALFVEC(1024), nullable=False),
        sa.CheckConstraint("dimensions = 1024", name="disclosure_narrative_dimensions_check"),
        sa.ForeignKeyConstraint(
            ["index_id", "dimensions"],
            ["disclosure_indexes.id", "disclosure_indexes.dimensions"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("index_id", "ordinal"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM disclosure_indexes WHERE dimensions=1024)")
    ).scalar_one():
        raise RuntimeError(
            "Remove 1024 indexes explicitly before downgrade; legacy vectors are retained"
        )
    op.drop_table("disclosure_narrative_chunks")
    op.drop_index("disclosure_indexes_active_idx", table_name="disclosure_indexes")
    op.create_index(
        "disclosure_indexes_active_idx",
        "disclosure_indexes",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.drop_constraint("disclosure_indexes_id_dimensions_key", "disclosure_indexes")
    op.drop_constraint("disclosure_indexes_identity_check", "disclosure_indexes")
    op.drop_column("disclosure_indexes", "identity")
    op.drop_constraint("disclosure_indexes_dimensions_check", "disclosure_indexes")
    op.create_check_constraint(
        "disclosure_indexes_dimensions_check", "disclosure_indexes", "dimensions = 2000"
    )
