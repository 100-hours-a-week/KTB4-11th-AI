import configparser
import pathlib

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REPO_ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / "alembic.ini").is_file()
)
MIGRATIONS_DIR = "infrastructure/postgres/migrations"


def _alembic_config_without_interpolation() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    parser.read(REPO_ROOT / "alembic.ini")
    return parser


def test_alembic_ini_lives_at_the_repository_root():
    assert (REPO_ROOT / "alembic.ini").is_file()


def test_script_location_points_at_infrastructure():
    location = _alembic_config_without_interpolation()["alembic"]["script_location"]

    assert location.endswith(MIGRATIONS_DIR)


def test_no_database_url_is_committed():
    assert "sqlalchemy.url" not in _alembic_config_without_interpolation()["alembic"]


def test_versions_directory_exists():
    assert (REPO_ROOT / MIGRATIONS_DIR / "versions").is_dir()


def _alembic_config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def _embedding_column_type(conn) -> str | None:
    return conn.execute(
        sa.text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = to_regclass('articles') AND attname = 'embedding'"
        )
    ).scalar()


def test_upgrade_creates_articles_with_a_2000_dimension_vector(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)

    command.upgrade(_alembic_config(), "head")

    with pg_engine.connect() as conn:
        assert _embedding_column_type(conn) == "vector(2000)"
        indexes = set(
            conn.execute(
                sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'articles'")
            ).scalars()
        )
    assert {"articles_embedding_hnsw", "articles_published_at_idx"} <= indexes


def test_downgrade_removes_articles_and_upgrade_restores_it(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()

    command.downgrade(config, "base")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('articles')")).scalar() is None

    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        assert _embedding_column_type(conn) == "vector(2000)"
