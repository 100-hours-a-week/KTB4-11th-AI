import sqlalchemy as sa

metadata = sa.MetaData()

disclosure_sections = sa.Table(
    "disclosure_sections",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("document_id", sa.BigInteger, nullable=False),
    sa.Column("receipt_id", sa.Text, nullable=False),
    sa.Column("document_name", sa.Text, nullable=False),
    sa.Column("document_order", sa.Integer, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("heading", sa.Text, nullable=False),
    sa.Column("topic", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("start_offset", sa.Integer, nullable=False),
    sa.Column("end_offset", sa.Integer, nullable=False),
    sa.Column("parser_version", sa.Text, nullable=False),
)

disclosure_financial_facts = sa.Table(
    "disclosure_financial_facts",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
    sa.Column("receipt_id", sa.Text, nullable=False),
    sa.Column("stock_code", sa.Text, nullable=False),
    sa.Column("business_year", sa.Integer, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("period_end", sa.Date, nullable=False),
    sa.Column("scope", sa.Text, nullable=False),
    sa.Column("statement_type", sa.Text, nullable=False),
    sa.Column("account_id", sa.Text, nullable=False),
    sa.Column("account_name", sa.Text, nullable=False),
    sa.Column("currency", sa.Text, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("source_value", sa.Text, nullable=False),
    sa.Column("value", sa.Numeric(38, 2), nullable=True),
    sa.Column("normalized", sa.Boolean, nullable=False),
)
