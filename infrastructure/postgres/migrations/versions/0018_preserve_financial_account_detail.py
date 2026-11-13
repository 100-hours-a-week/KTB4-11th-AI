import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("disclosure_financial_facts", sa.Column("account_detail", sa.Text))
    op.add_column("disclosure_financial_facts", sa.Column("source_ordinal", sa.Text))


def downgrade() -> None:
    op.drop_column("disclosure_financial_facts", "source_ordinal")
    op.drop_column("disclosure_financial_facts", "account_detail")
