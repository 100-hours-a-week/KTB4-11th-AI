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
