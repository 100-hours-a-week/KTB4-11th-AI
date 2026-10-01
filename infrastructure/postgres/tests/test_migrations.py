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
        ("portfolio_builder.database", {"portfolios", "portfolio_holdings", "portfolio_exits"}),
        (
            "portfolio_rebalancer.database",
            {
                "users",
                "accounts",
                "account_holdings",
                "account_pending_orders",
                "rebalance_orders",
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
        if type_ == "index" and name == "cluster_summaries_fts_idx":
            return False
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
                    "INSERT INTO theme_companies (theme_code, stock_code, is_major)"
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


# Every key GET /api/v1/users/ai-server carries, against the column it has to reach. Since
# 0008 each one lands under its own name.
POLL_FIELDS = {
    "users": {"user_id": "user_id"},
    "accounts": {
        "account_id": "account_id",
        "account_name": "account_name",
        "is_active": "is_active",
        "cash_balance": "cash_balance",
    },
    "account_holdings": {
        "stock_code": "stock_code",
        "quantity": "quantity",
        "total_cost": "total_cost",
    },
    "account_pending_orders": {
        "order_id": "order_id",
        "order_side": "order_side",
        "order_type": "order_type",
        "order_status": "order_status",
        "stock_code": "stock_code",
        "limit_price": "limit_price",
        "quantity": "quantity",
        "current_stock_price": "current_stock_price",
    },
}


def test_no_field_the_poll_carries_is_dropped(pg_dsn, pg_engine, monkeypatch):
    """A field with no column would be silently lost on every poll."""
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")

    with pg_engine.connect() as conn:
        missing = {
            f"{table}.{payload_key} -> {column}"
            for table, fields in POLL_FIELDS.items()
            for payload_key, column in fields.items()
            if column not in _columns(conn, table)
        }

    assert missing == set()


def test_a_repeated_rebalance_of_the_same_account_cannot_be_recorded_twice(
    pg_dsn, pg_engine, monkeypatch
):
    """The unique constraint is what makes a second rebalance a no-op rather than a
    duplicate order."""
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")

    with pg_engine.connect() as conn:
        constraints = set(
            conn.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = to_regclass('rebalance_orders') AND contype = 'u'"
                )
            ).scalars()
        )

    assert "rebalance_orders_portfolio_account_stock_key" in constraints


def test_downgrade_to_0006_removes_the_rebalance_tables(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()
    command.upgrade(config, "head")

    command.downgrade(config, "0006")

    with pg_engine.connect() as conn:
        for table in (
            "users",
            "accounts",
            "account_holdings",
            "account_pending_orders",
            "rebalance_orders",
        ):
            assert conn.execute(sa.text(f"SELECT to_regclass('{table}')")).scalar() is None

    command.upgrade(config, "head")


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


def test_upgrade_to_0006_rekeys_every_reference_by_stock_code(pg_dsn, pg_engine, monkeypatch):
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
                    "SELECT theme_code, stock_code, is_major FROM theme_companies"
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


def test_downgrade_from_0006_restores_corp_code_links(pg_dsn, pg_engine, monkeypatch):
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
        command.upgrade(config, "0006")

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


def _seed_portfolio_0005(conn) -> None:
    portfolio_id = conn.execute(
        sa.text(
            "INSERT INTO portfolios (cash_weight, commentary, model)"
            " VALUES (0.2, '총평', 'm') RETURNING id"
        )
    ).scalar_one()
    conn.execute(
        sa.text(
            "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight, reason)"
            " VALUES (:p, '00126380', 0.8, '편입')"
        ),
        {"p": portfolio_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
            " VALUES (:p, '00164779', '편출')"
        ),
        {"p": portfolio_id},
    )


def test_0006_round_trip_keeps_portfolio_rows_linked_by_corp_code(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()
    foreign_keys_query = sa.text(
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE contype = 'f' AND conrelid IN"
        " ('portfolio_holdings'::regclass, 'portfolio_exits'::regclass)"
        " ORDER BY 1, 2"
    )
    command.downgrade(config, "0004")
    command.upgrade(config, "0005")
    try:
        with pg_engine.begin() as conn:
            foreign_keys_at_0005 = conn.execute(foreign_keys_query).all()
            _seed_0004(conn)
            _seed_portfolio_0005(conn)

        command.upgrade(config, "head")

        with pg_engine.connect() as conn:
            holdings = conn.execute(
                sa.text(
                    "SELECT h.company_id, c.stock_code FROM portfolio_holdings h"
                    " JOIN corporations c ON c.corp_code = h.company_id"
                )
            ).all()
            exits = conn.execute(
                sa.text(
                    "SELECT e.company_id, c.stock_code FROM portfolio_exits e"
                    " JOIN corporations c ON c.corp_code = e.company_id"
                )
            ).all()
            foreign_keys = conn.execute(foreign_keys_query).all()
        assert holdings == [("00126380", "005930")]
        assert exits == [("00164779", "000660")]
        target = "REFERENCES corporations(corp_code)"
        assert [(table, definition) for table, _, definition in foreign_keys] == [
            ("portfolio_exits", f"FOREIGN KEY (company_id) {target}"),
            (
                "portfolio_exits",
                "FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE",
            ),
            ("portfolio_holdings", f"FOREIGN KEY (company_id) {target}"),
            (
                "portfolio_holdings",
                "FOREIGN KEY (portfolio_id) REFERENCES portfolios(id) ON DELETE CASCADE",
            ),
        ]

        command.downgrade(config, "0005")

        with pg_engine.connect() as conn:
            assert conn.execute(foreign_keys_query).all() == foreign_keys_at_0005
            holdings = conn.execute(
                sa.text(
                    "SELECT h.company_id, c.corp_name FROM portfolio_holdings h"
                    " JOIN companies c ON c.corp_code = h.company_id"
                )
            ).all()
            exits = conn.execute(
                sa.text(
                    "SELECT e.company_id, c.corp_name FROM portfolio_exits e"
                    " JOIN companies c ON c.corp_code = e.company_id"
                )
            ).all()
        assert holdings == [("00126380", "삼성전자")]
        assert exits == [("00164779", "SK하이닉스")]
    finally:
        command.upgrade(config, "head")
        with pg_engine.begin() as conn:
            conn.execute(sa.text("TRUNCATE portfolios CASCADE"))
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


def test_0006_keys_reference_tables_by_stock_code(pg_dsn, pg_engine, monkeypatch):
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
        assert {"is_major"} <= _columns(conn, "theme_companies")
        assert "is_main" not in _columns(conn, "theme_companies")

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


def test_portfolio_rows_reference_corporations_and_cascade_from_portfolios(
    pg_dsn, pg_engine, monkeypatch
):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")
    try:
        with pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO corporations (stock_code, corp_code, name)"
                    " VALUES ('005930', '00126380', '삼성전자')"
                )
            )
            portfolio_id = conn.execute(
                sa.text(
                    "INSERT INTO portfolios (cash_weight, commentary, model)"
                    " VALUES (0.2, '총평', 'openai-codex/gpt-5.5') RETURNING id"
                )
            ).scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO portfolio_holdings (portfolio_id, company_id, weight, reason)"
                    " VALUES (:p, '00126380', 0.8, '편입 사유')"
                ),
                {"p": portfolio_id},
            )
            holding = conn.execute(
                sa.text("SELECT cited_cluster_ids FROM portfolio_holdings")
            ).scalar_one()
        assert holding == []

        with pytest.raises(sa.exc.IntegrityError), pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO portfolio_exits (portfolio_id, company_id, reason)"
                    " VALUES (:p, '99999999', '없는 회사')"
                ),
                {"p": portfolio_id},
            )

        with pg_engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM portfolios"))
            remaining = conn.execute(
                sa.text("SELECT count(*) FROM portfolio_holdings")
            ).scalar_one()
        assert remaining == 0
    finally:
        with pg_engine.begin() as conn:
            conn.execute(sa.text("TRUNCATE portfolios, corporations CASCADE"))


def test_cluster_summaries_have_a_full_text_index(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    command.upgrade(_alembic_config(), "head")
    with pg_engine.connect() as conn:
        definition = conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'cluster_summaries_fts_idx'")
        ).scalar_one()
    assert "to_tsvector('simple'" in definition


def test_downgrade_to_0004_removes_the_portfolio_tables(pg_dsn, pg_engine, monkeypatch):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = _alembic_config()

    command.downgrade(config, "0004")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('portfolios')")).scalar() is None
        assert (
            conn.execute(sa.text("SELECT to_regclass('cluster_summaries_fts_idx')")).scalar()
            is None
        )

    command.upgrade(config, "head")
    with pg_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT to_regclass('portfolios')")).scalar() is not None
