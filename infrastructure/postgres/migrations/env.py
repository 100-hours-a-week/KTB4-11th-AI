import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

DSN_ENV = "KTB_POSTGRES_DSN"


def _database_url() -> str:
    url = os.environ.get(DSN_ENV)
    if not url:
        raise RuntimeError(
            f"{DSN_ENV} is not set. Example: "
            f"{DSN_ENV}=postgresql+psycopg://ktb:ktb@localhost:5432/news"
        )
    return url


config.set_main_option("sqlalchemy.url", _database_url())

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
