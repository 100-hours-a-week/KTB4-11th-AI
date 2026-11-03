import importlib.util
import io
import pathlib
import re

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "versions"


@pytest.mark.parametrize(
    ("revision", "tables", "indexes"),
    [
        (
            "0013",
            ["disclosures", "disclosure_documents", "disclosure_collection_state"],
            ["disclosures_company_date_idx"],
        ),
        ("0014", ["disclosure_sections"], ["disclosure_sections_receipt_idx"]),
        (
            "0015",
            ["disclosure_financial_facts"],
            ["disclosure_financial_facts_company_idx", "disclosure_financial_facts_period_idx"],
        ),
    ],
)
def test_collection_revision_owns_its_tables_and_reverses_only_its_changes(
    revision, tables, indexes
):
    path = next(VERSIONS.glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == revision
    assert migration.down_revision == f"{int(revision) - 1:04}"

    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
    upgrade = output.getvalue()
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == tables
    assert re.findall(r"CREATE INDEX (\w+)", upgrade) == indexes
    assert "DROP " not in upgrade
    if revision == "0015":
        assert (
            "ALTER TABLE disclosures ADD COLUMN financials_collected BOOLEAN DEFAULT false NOT NULL"
            in upgrade
        )
    else:
        assert "ALTER TABLE " not in upgrade
    assert "halfvec" not in upgrade and "disclosure_indexes" not in upgrade
    assert "REFERENCES corporations (stock_code)" in upgrade or revision == "0014"
    if revision != "0013":
        assert "REFERENCES disclosures (receipt_id) ON DELETE CASCADE" in upgrade
    if revision == "0013":
        assert "UNIQUE (receipt_id, source_hash, parser_version)" in upgrade
        assert "detail_type TEXT" in upgrade
        assert "business_year INTEGER," in upgrade
        assert "period_end DATE," in upgrade
        assert "CONSTRAINT disclosures_attempts_check CHECK (attempts BETWEEN 0 AND 5)" in upgrade
    if revision == "0014":
        assert "REFERENCES disclosure_documents (id) ON DELETE CASCADE" in upgrade
        assert "UNIQUE (document_id, document_name, ordinal)" in upgrade
        assert (
            "CONSTRAINT disclosure_sections_range_check CHECK (end_offset >= start_offset)"
            in upgrade
        )
    if revision == "0015":
        assert "UNIQUE (receipt_id, scope, statement_type, account_id, ordinal)" in upgrade
        assert (
            "CONSTRAINT disclosure_financial_facts_scope_check CHECK (scope IN ('CFS','OFS'))"
            in upgrade
        )

    output.seek(0)
    output.truncate()
    with Operations.context(context):
        migration.downgrade()
    downgrade = output.getvalue()
    assert re.findall(r"DROP TABLE (\w+)", downgrade) == list(reversed(tables))
    assert re.findall(r"DROP INDEX (\w+)", downgrade) == list(reversed(indexes))
    assert "CREATE " not in downgrade
    if revision == "0015":
        assert "ALTER TABLE disclosures DROP COLUMN financials_collected" in downgrade
    else:
        assert "ALTER TABLE " not in downgrade
    for name in indexes:
        assert downgrade.index(f"DROP INDEX {name}") < downgrade.index(f"DROP TABLE {tables[0]}")


def test_collection_migrations_extend_dev_as_one_unique_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(Config(str(VERSIONS.parents[3] / "alembic.ini")))
    revisions = list(scripts.walk_revisions())
    assert len({revision.revision for revision in revisions}) == len(revisions)
    assert scripts.get_heads() == ["0015"]
    assert scripts.get_revision("0013").down_revision == "0012"
    assert scripts.get_revision("0012").path.endswith("0012_rename_theme_corporations.py")


def test_collection_upgrade_and_rollback_preserve_dev_theme_data(pg_dsn, pg_engine, monkeypatch):
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = Config(str(VERSIONS.parents[3] / "alembic.ini"))
    command.downgrade(config, "0012")
    try:
        with pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO corporations(stock_code,corp_code,name) "
                    "VALUES ('990009','00990009','migration test')"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO themes(theme_code,name) VALUES ('review-441','migration test')"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO theme_corporations(theme_code,stock_code,is_major) "
                    "VALUES ('review-441','990009',true)"
                )
            )
        for revision in ("head", "0012", "head"):
            if revision == "0012":
                command.downgrade(config, revision)
            else:
                command.upgrade(config, revision)
            with pg_engine.connect() as conn:
                assert conn.execute(
                    sa.text(
                        "SELECT stock_code,is_major FROM theme_corporations "
                        "WHERE theme_code='review-441'"
                    )
                ).all() == [("990009", True)]
                assert conn.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalar_one() == ("0012" if revision == "0012" else "0015")
                assert (
                    conn.execute(sa.text("SELECT to_regclass('disclosures')")).scalar() is None
                ) == (revision == "0012")
    finally:
        command.upgrade(config, "head")
        with pg_engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM theme_corporations WHERE theme_code='review-441'"))
            conn.execute(sa.text("DELETE FROM themes WHERE theme_code='review-441'"))
            conn.execute(sa.text("DELETE FROM corporations WHERE stock_code='990009'"))
