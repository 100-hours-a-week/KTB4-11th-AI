import configparser
import importlib
import pathlib

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

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


@pytest.mark.parametrize(
    ("module", "owned_tables"),
    [
        ("news_preprocessor.storage", {"articles"}),
        ("news_clusterer.storage", {"clusters", "article_clusters"}),
        (
            "news_graph_builder.database",
            {
                "companies",
                "company_aliases",
                "entities",
                "cluster_summaries",
                "cluster_entities",
                "relations",
            },
        ),
    ],
)
def test_service_tables_match_the_migrated_schema(
    pg_dsn, pg_engine, monkeypatch, module, owned_tables
):
    metadata = importlib.import_module(module).metadata

    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")

    def only_owned_tables(obj, name, type_, reflected, compare_to):
        return name in owned_tables if type_ == "table" else True

    with pg_engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "include_object": only_owned_tables}
        )
        assert compare_metadata(context, metadata) == []


def test_downgrade_removes_the_cluster_tables(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()

    command.downgrade(config, "0001")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('clusters')")).scalar() is None

    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('clusters')")).scalar() is not None


def _columns(conn, table: str) -> set[str]:
    return set(
        conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        ).scalars()
    )


def test_summaries_live_outside_clusters(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)

    command.upgrade(_alembic_config(), "head")

    with pg_engine.connect() as conn:
        assert _columns(conn, "clusters") == {"id", "updated_at"}
        assert _columns(conn, "cluster_summaries") == {
            "cluster_id",
            "title",
            "summary",
            "cluster_updated_at",
            "summarized_at",
        }


def test_downgrade_to_0002_copies_summaries_back(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()
    command.upgrade(config, "head")
    with pg_engine.begin() as conn:
        cluster_id = conn.execute(
            sa.text("INSERT INTO clusters DEFAULT VALUES RETURNING id")
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO cluster_summaries (cluster_id, title, summary, cluster_updated_at)"
                " VALUES (:id, '제목', '요약', now())"
            ),
            {"id": cluster_id},
        )

    try:
        command.downgrade(config, "0002")
        with pg_engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT title, summary, summarized_at FROM clusters WHERE id = :id"),
                {"id": cluster_id},
            ).one()
        assert (row.title, row.summary) == ("제목", "요약")
        assert row.summarized_at is not None
    finally:
        command.upgrade(config, "head")
        with pg_engine.begin() as conn:
            conn.execute(sa.text("TRUNCATE clusters CASCADE"))
