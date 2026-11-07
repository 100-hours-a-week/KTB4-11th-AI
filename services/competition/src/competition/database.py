import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()

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
