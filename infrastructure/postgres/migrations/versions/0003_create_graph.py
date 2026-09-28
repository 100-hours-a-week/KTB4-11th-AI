"""move cluster summaries out of clusters and create the knowledge graph

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
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
    op.create_table(
        "company_aliases",
        sa.Column("alias", sa.Text, primary_key=True),
        sa.Column("corp_code", sa.Text, sa.ForeignKey("companies.corp_code"), nullable=False),
    )
    op.create_table(
        "entities",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("raw_name", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("corp_code", sa.Text, sa.ForeignKey("companies.corp_code"), nullable=True),
    )
    op.create_index(
        "entities_corp_code_key",
        "entities",
        ["corp_code"],
        unique=True,
        postgresql_where=sa.text("corp_code IS NOT NULL"),
    )
    op.create_index(
        "entities_name_type_key",
        "entities",
        ["name", "type"],
        unique=True,
        postgresql_where=sa.text("corp_code IS NULL"),
    )
    op.create_table(
        "cluster_summaries",
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
    op.create_table(
        "cluster_entities",
        sa.Column(
            "cluster_id",
            sa.BigInteger,
            sa.ForeignKey("clusters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("entity_id", sa.BigInteger, sa.ForeignKey("entities.id"), primary_key=True),
    )
    op.create_index("cluster_entities_entity_id_idx", "cluster_entities", ["entity_id"])
    op.create_table(
        "relations",
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
    )
    op.create_index("relations_cluster_id_idx", "relations", ["cluster_id"])
    op.create_index("relations_source_entity_id_idx", "relations", ["source_entity_id"])
    op.create_index("relations_target_entity_id_idx", "relations", ["target_entity_id"])
    op.drop_column("clusters", "title")
    op.drop_column("clusters", "summary")
    op.drop_column("clusters", "summarized_at")


def downgrade() -> None:
    op.add_column("clusters", sa.Column("title", sa.Text, nullable=True))
    op.add_column("clusters", sa.Column("summary", sa.Text, nullable=True))
    op.add_column("clusters", sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE clusters SET title = s.title, summary = s.summary,"
        " summarized_at = s.summarized_at"
        " FROM cluster_summaries s WHERE s.cluster_id = clusters.id"
    )
    op.drop_table("relations")
    op.drop_table("cluster_entities")
    op.drop_table("cluster_summaries")
    op.drop_table("entities")
    op.drop_table("company_aliases")
    op.drop_table("companies")
