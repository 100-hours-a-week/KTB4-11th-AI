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
            "0012",
            ["disclosures", "disclosure_documents", "disclosure_collection_progress"],
            ["disclosures_company_date_idx"],
        ),
        ("0013", ["disclosure_sections"], ["disclosure_sections_receipt_idx"]),
        (
            "0014",
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
    if revision == "0014":
        assert (
            "ALTER TABLE disclosures ADD COLUMN financials_collected BOOLEAN DEFAULT false NOT NULL"
            in upgrade
        )
    else:
        assert "ALTER TABLE " not in upgrade
    assert "halfvec" not in upgrade and "disclosure_indexes" not in upgrade
    assert "REFERENCES corporations (stock_code)" in upgrade or revision == "0013"
    if revision != "0012":
        assert "REFERENCES disclosures (receipt_id) ON DELETE CASCADE" in upgrade
    if revision == "0012":
        assert "UNIQUE (receipt_id, source_hash, parser_version)" in upgrade
        assert (
            "PRIMARY KEY (stock_code, start_date, end_date, business_year, include_current)"
            in upgrade
        )
        assert "CONSTRAINT disclosures_attempts_check CHECK (attempts BETWEEN 0 AND 5)" in upgrade
    if revision == "0013":
        assert "REFERENCES disclosure_documents (id) ON DELETE CASCADE" in upgrade
        assert "UNIQUE (document_id, document_name, ordinal)" in upgrade
        assert (
            "CONSTRAINT disclosure_sections_range_check CHECK (end_offset >= start_offset)"
            in upgrade
        )
    if revision == "0014":
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
    if revision == "0014":
        assert "ALTER TABLE disclosures DROP COLUMN financials_collected" in downgrade
    else:
        assert "ALTER TABLE " not in downgrade
    for name in indexes:
        assert downgrade.index(f"DROP INDEX {name}") < downgrade.index(f"DROP TABLE {tables[0]}")
