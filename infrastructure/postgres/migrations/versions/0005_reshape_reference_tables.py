"""reshape reference tables: companies -> corporations keyed by stock_code

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

REFERENCING_TABLES = ("entities", "company_aliases", "theme_companies")


def upgrade() -> None:
    for table in REFERENCING_TABLES:
        op.add_column(table, sa.Column("stock_code", sa.Text, nullable=True))
        op.execute(
            f"UPDATE {table} SET stock_code = c.stock_code FROM companies c"
            f" WHERE {table}.corp_code = c.corp_code"
        )
        op.drop_constraint(f"{table}_corp_code_fkey", table, type_="foreignkey")

    op.drop_index("entities_corp_code_key", table_name="entities")
    op.drop_index("entities_name_type_key", table_name="entities")
    op.drop_index("theme_companies_corp_code_idx", table_name="theme_companies")
    op.drop_constraint("theme_companies_pkey", "theme_companies", type_="primary")

    op.drop_constraint("companies_pkey", "companies", type_="primary")
    op.rename_table("companies", "corporations")
    op.alter_column("corporations", "corp_name", new_column_name="name")
    op.alter_column("corporations", "corp_eng_name", new_column_name="eng_name")
    op.create_primary_key("corporations_pkey", "corporations", ["stock_code"])
    op.create_unique_constraint("corporations_corp_code_key", "corporations", ["corp_code"])
    op.add_column(
        "corporations", sa.Column("market", sa.Text, nullable=False, server_default="KOSPI")
    )

    op.rename_table("company_aliases", "corporation_aliases")
    op.execute(
        "ALTER TABLE corporation_aliases"
        " RENAME CONSTRAINT company_aliases_pkey TO corporation_aliases_pkey"
    )

    op.alter_column("corporation_aliases", "stock_code", nullable=False)
    op.alter_column("theme_companies", "stock_code", nullable=False)
    op.create_primary_key("theme_companies_pkey", "theme_companies", ["theme_code", "stock_code"])
    op.create_index("theme_companies_stock_code_idx", "theme_companies", ["stock_code"])

    op.create_foreign_key(
        "corporation_aliases_stock_code_fkey",
        "corporation_aliases",
        "corporations",
        ["stock_code"],
        ["stock_code"],
    )
    op.create_foreign_key(
        "theme_companies_stock_code_fkey",
        "theme_companies",
        "corporations",
        ["stock_code"],
        ["stock_code"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "entities_stock_code_fkey", "entities", "corporations", ["stock_code"], ["stock_code"]
    )
    op.create_index(
        "entities_stock_code_key",
        "entities",
        ["stock_code"],
        unique=True,
        postgresql_where=sa.text("stock_code IS NOT NULL"),
    )
    op.create_index(
        "entities_name_type_key",
        "entities",
        ["name", "type"],
        unique=True,
        postgresql_where=sa.text("stock_code IS NULL"),
    )

    op.drop_column("entities", "corp_code")
    op.drop_column("corporation_aliases", "corp_code")
    op.drop_column("theme_companies", "corp_code")
    op.alter_column("theme_companies", "is_main", new_column_name="is_major")

    op.create_table(
        "corporation_indices",
        sa.Column(
            "stock_code", sa.Text, sa.ForeignKey("corporations.stock_code"), primary_key=True
        ),
        sa.Column("index_name", sa.Text, primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("corporation_indices")
    op.alter_column("theme_companies", "is_major", new_column_name="is_main")

    op.drop_constraint(
        "corporation_aliases_stock_code_fkey", "corporation_aliases", type_="foreignkey"
    )
    op.drop_constraint("theme_companies_stock_code_fkey", "theme_companies", type_="foreignkey")
    op.drop_constraint("entities_stock_code_fkey", "entities", type_="foreignkey")
    op.rename_table("corporation_aliases", "company_aliases")
    op.execute(
        "ALTER TABLE company_aliases"
        " RENAME CONSTRAINT corporation_aliases_pkey TO company_aliases_pkey"
    )

    for table in REFERENCING_TABLES:
        op.add_column(table, sa.Column("corp_code", sa.Text, nullable=True))
        op.execute(
            f"UPDATE {table} SET corp_code = c.corp_code FROM corporations c"
            f" WHERE {table}.stock_code = c.stock_code"
        )

    op.drop_index("entities_stock_code_key", table_name="entities")
    op.drop_index("entities_name_type_key", table_name="entities")
    op.drop_index("theme_companies_stock_code_idx", table_name="theme_companies")
    op.drop_constraint("theme_companies_pkey", "theme_companies", type_="primary")

    op.drop_column("corporations", "market")
    op.drop_constraint("corporations_corp_code_key", "corporations", type_="unique")
    op.drop_constraint("corporations_pkey", "corporations", type_="primary")
    op.alter_column("corporations", "name", new_column_name="corp_name")
    op.alter_column("corporations", "eng_name", new_column_name="corp_eng_name")
    op.rename_table("corporations", "companies")
    op.create_primary_key("companies_pkey", "companies", ["corp_code"])

    op.alter_column("company_aliases", "corp_code", nullable=False)
    op.alter_column("theme_companies", "corp_code", nullable=False)
    op.create_primary_key("theme_companies_pkey", "theme_companies", ["theme_code", "corp_code"])
    op.create_index("theme_companies_corp_code_idx", "theme_companies", ["corp_code"])

    op.create_foreign_key(
        "company_aliases_corp_code_fkey",
        "company_aliases",
        "companies",
        ["corp_code"],
        ["corp_code"],
    )
    op.create_foreign_key(
        "theme_companies_corp_code_fkey",
        "theme_companies",
        "companies",
        ["corp_code"],
        ["corp_code"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "entities_corp_code_fkey", "entities", "companies", ["corp_code"], ["corp_code"]
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

    for table in REFERENCING_TABLES:
        op.drop_column(table, "stock_code")
