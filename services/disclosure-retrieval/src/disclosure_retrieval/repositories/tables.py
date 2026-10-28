import sqlalchemy as sa

metadata = sa.MetaData()

disclosures = sa.Table(
    "disclosures",
    metadata,
    sa.Column("receipt_id", sa.Text, primary_key=True),
    sa.Column("stock_code", sa.Text, nullable=False),
    sa.Column("business_year", sa.Integer, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("period_end", sa.Date, nullable=False),
    sa.Column("published_date", sa.Date, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
)

disclosure_sections = sa.Table(
    "disclosure_sections",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("receipt_id", sa.Text, sa.ForeignKey("disclosures.receipt_id"), nullable=False),
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
