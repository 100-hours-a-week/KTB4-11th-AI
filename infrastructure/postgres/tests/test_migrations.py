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
            "market_syncer.database",
            {
                "corporations",
                "corporation_aliases",
                "corporation_indices",
                "themes",
                "theme_companies",
            },
        ),
        (
            "news_graph_builder.database",
            {
                "corporations",
                "corporation_aliases",
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


def test_theme_memberships_cascade_from_themes_and_corporations(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")
    try:
        with pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO corporations (stock_code, corp_code, name)"
                    " VALUES ('005930', '00126380', '삼성전자'), ('000660', '00164779',"
                    " 'SK하이닉스')"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO themes (theme_code, name) VALUES ('1', 'HBM'), ('2', '반도체')"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO theme_companies (theme_code, stock_code, is_main)"
                    " VALUES ('1', '005930', true), ('2', '000660', false)"
                )
            )
        with pg_engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM themes WHERE theme_code = '1'"))
            conn.execute(sa.text("DELETE FROM corporations WHERE stock_code = '000660'"))
        with pg_engine.connect() as conn:
            remaining = conn.execute(sa.text("SELECT count(*) FROM theme_companies")).scalar_one()
        assert remaining == 0
    finally:
        with pg_engine.begin() as conn:
            conn.execute(sa.text("TRUNCATE theme_companies, themes, corporations CASCADE"))


def test_downgrade_to_0003_removes_the_theme_tables(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()

    command.downgrade(config, "0003")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('themes')")).scalar() is None
        assert conn.execute(sa.text("SELECT to_regclass('theme_companies')")).scalar() is None

    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('theme_companies')")).scalar() is not None


def _seed_0004(conn) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO companies (corp_code, stock_code, corp_name, corp_eng_name)"
            " VALUES ('00126380', '005930', '삼성전자', 'Samsung Electronics'),"
            " ('00164779', '000660', 'SK하이닉스', NULL)"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO company_aliases (alias, corp_code)"
            " VALUES ('삼성', '00126380'), ('하이닉스', '00164779')"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO entities (raw_name, name, type, corp_code)"
            " VALUES ('삼성', '삼성전자', 'CORPORATION', '00126380'),"
            " ('애플', '애플', 'CORPORATION', NULL)"
        )
    )
    conn.execute(sa.text("INSERT INTO themes (theme_code, name) VALUES ('1', 'HBM')"))
    conn.execute(
        sa.text(
            "INSERT INTO theme_companies (theme_code, corp_code, is_main)"
            " VALUES ('1', '00126380', true), ('1', '00164779', false)"
        )
    )


def _truncate_reference_tables(pg_engine) -> None:
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                "TRUNCATE corporation_indices, theme_companies, themes, corporation_aliases,"
                " entities, corporations CASCADE"
            )
        )


def test_upgrade_to_0005_rekeys_every_reference_by_stock_code(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()
    command.downgrade(config, "0004")
    try:
        with pg_engine.begin() as conn:
            _seed_0004(conn)

        command.upgrade(config, "head")

        with pg_engine.connect() as conn:
            corporations = conn.execute(
                sa.text(
                    "SELECT stock_code, corp_code, name, eng_name, market"
                    " FROM corporations ORDER BY stock_code"
                )
            ).all()
            aliases = conn.execute(
                sa.text("SELECT alias, stock_code FROM corporation_aliases ORDER BY alias")
            ).all()
            entities = conn.execute(
                sa.text("SELECT raw_name, stock_code FROM entities ORDER BY raw_name")
            ).all()
            memberships = conn.execute(
                sa.text(
                    "SELECT theme_code, stock_code, is_main FROM theme_companies"
                    " ORDER BY stock_code"
                )
            ).all()
        assert corporations == [
            ("000660", "00164779", "SK하이닉스", None, "KOSPI"),
            ("005930", "00126380", "삼성전자", "Samsung Electronics", "KOSPI"),
        ]
        assert aliases == [("삼성", "005930"), ("하이닉스", "000660")]
        assert entities == [("삼성", "005930"), ("애플", None)]
        assert memberships == [("1", "000660", False), ("1", "005930", True)]
    finally:
        _truncate_reference_tables(pg_engine)


def test_downgrade_from_0005_restores_corp_code_links(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()
    schema_query = sa.text(
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE connamespace = 'public'::regnamespace"
        " UNION SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
        " UNION SELECT table_name, column_name, data_type || ' ' || is_nullable"
        " || ' ' || coalesce(column_default, '')"
        " FROM information_schema.columns WHERE table_schema = 'public'"
    )
    command.downgrade(config, "0002")
    command.upgrade(config, "0004")
    try:
        with pg_engine.begin() as conn:
            schema_at_0004 = set(conn.execute(schema_query).all())
            _seed_0004(conn)
        command.upgrade(config, "0005")

        command.downgrade(config, "0004")

        with pg_engine.connect() as conn:
            assert set(conn.execute(schema_query).all()) == schema_at_0004
            assert conn.execute(sa.text("SELECT to_regclass('corporations')")).scalar() is None
            assert _columns(conn, "companies") == {
                "corp_code",
                "stock_code",
                "corp_name",
                "corp_eng_name",
                "synced_at",
            }
            aliases = conn.execute(
                sa.text("SELECT alias, corp_code FROM company_aliases ORDER BY alias")
            ).all()
            entities = conn.execute(
                sa.text("SELECT raw_name, corp_code FROM entities ORDER BY raw_name")
            ).all()
            memberships = conn.execute(
                sa.text(
                    "SELECT theme_code, corp_code, is_main FROM theme_companies ORDER BY corp_code"
                )
            ).all()
            assert "stock_code" not in _columns(conn, "entities")
        assert aliases == [("삼성", "00126380"), ("하이닉스", "00164779")]
        assert entities == [("삼성", "00126380"), ("애플", None)]
        assert memberships == [("1", "00126380", True), ("1", "00164779", False)]
    finally:
        command.upgrade(config, "head")
        _truncate_reference_tables(pg_engine)


def _primary_key_columns(conn, table: str) -> tuple[str, list[str]]:
    row = conn.execute(
        sa.text(
            "SELECT c.conname, array_agg(a.attname ORDER BY k.ord)"
            " FROM pg_constraint c"
            " CROSS JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)"
            " JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum"
            " WHERE c.conrelid = to_regclass(:t) AND c.contype = 'p'"
            " GROUP BY c.conname"
        ),
        {"t": table},
    ).one()
    return row[0], list(row[1])


def _constraints(conn, table: str, contype: str) -> dict[str, str]:
    return dict(
        conn.execute(
            sa.text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid = to_regclass(:t) AND contype = :c"
            ),
            {"t": table, "c": contype},
        ).all()
    )


def _indexes(conn, table: str) -> dict[str, str]:
    return dict(
        conn.execute(
            sa.text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"),
            {"t": table},
        ).all()
    )


def test_0005_keys_reference_tables_by_stock_code(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")

    with pg_engine.connect() as conn:
        for table in ("companies", "company_aliases"):
            assert conn.execute(sa.text("SELECT to_regclass(:t)"), {"t": table}).scalar() is None
        assert _primary_key_columns(conn, "corporations") == ("corporations_pkey", ["stock_code"])
        assert _primary_key_columns(conn, "corporation_aliases") == (
            "corporation_aliases_pkey",
            ["alias"],
        )
        assert _primary_key_columns(conn, "corporation_indices") == (
            "corporation_indices_pkey",
            ["stock_code", "index_name"],
        )
        assert _primary_key_columns(conn, "theme_companies") == (
            "theme_companies_pkey",
            ["theme_code", "stock_code"],
        )
        assert _constraints(conn, "corporations", "u") == {
            "corporations_corp_code_key": "UNIQUE (corp_code)"
        }
        assert _columns(conn, "corporations") == {
            "stock_code",
            "corp_code",
            "name",
            "eng_name",
            "market",
            "synced_at",
        }
        assert "corp_code" not in _columns(conn, "entities") | _columns(conn, "theme_companies")

        target = "REFERENCES corporations(stock_code)"
        assert _constraints(conn, "corporation_aliases", "f") == {
            "corporation_aliases_stock_code_fkey": f"FOREIGN KEY (stock_code) {target}"
        }
        assert _constraints(conn, "entities", "f") == {
            "entities_stock_code_fkey": f"FOREIGN KEY (stock_code) {target}"
        }
        assert _constraints(conn, "corporation_indices", "f") == {
            "corporation_indices_stock_code_fkey": f"FOREIGN KEY (stock_code) {target}"
        }
        assert (
            _constraints(conn, "theme_companies", "f")["theme_companies_stock_code_fkey"]
            == f"FOREIGN KEY (stock_code) {target} ON DELETE CASCADE"
        )

        entity_indexes = _indexes(conn, "entities")
        assert entity_indexes["entities_stock_code_key"].endswith(
            "USING btree (stock_code) WHERE (stock_code IS NOT NULL)"
        )
        assert entity_indexes["entities_name_type_key"].endswith(
            "USING btree (name, type) WHERE (stock_code IS NULL)"
        )
        assert "theme_companies_stock_code_idx" in _indexes(conn, "theme_companies")
