import importlib.util
import io
import pathlib
import re

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


def test_vector_revision_follows_collection_and_preserves_both_chunk_formats():
    migrations = pathlib.Path(__file__).resolve().parents[1] / "migrations"
    scripts = ScriptDirectory(str(migrations))
    assert scripts.get_heads() == ["0015"]
    migration = scripts.get_revision("0015")
    assert migration is not None and migration.down_revision == "0014"
    spec = importlib.util.spec_from_file_location("migration_0015", migration.path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        module.upgrade()
    upgrade = output.getvalue()
    tables = ["disclosure_indexes", "disclosure_chunks", "disclosure_narrative_chunks"]
    assert re.findall(r"CREATE TABLE (\w+)", upgrade) == tables
    assert re.findall(r"CREATE UNIQUE INDEX (\w+)", upgrade) == ["disclosure_indexes_active_idx"]
    assert re.findall(r"CREATE INDEX (\w+)", upgrade) == ["disclosure_indexes_work_idx"]
    assert "DROP " not in upgrade and "ALTER TABLE " not in upgrade
    assert "embedding HALFVEC(2000) NOT NULL" in upgrade
    assert "embedding HALFVEC(1024) NOT NULL" in upgrade
    assert "REFERENCES disclosure_documents (id) ON DELETE CASCADE" in upgrade
    assert "REFERENCES disclosure_indexes (id) ON DELETE CASCADE" in upgrade
    assert (
        "FOREIGN KEY(index_id, dimensions) REFERENCES disclosure_indexes (id, dimensions)"
        in upgrade
    )
    assert "CONSTRAINT disclosure_indexes_id_dimensions_key UNIQUE (id, dimensions)" in upgrade
    assert (
        "CONSTRAINT disclosure_indexes_dimensions_check CHECK (dimensions IN (1024, 2000))"
        in upgrade
    )
    assert "CONSTRAINT disclosure_narrative_dimensions_check CHECK (dimensions = 1024)" in upgrade
    assert "identity ? 'adapter_sha256'" in upgrade
    assert "identity->>'dimensions' = '1024' AND identity->>'max_tokens' = '16384'" in upgrade
    assert "ON disclosure_indexes (document_id, dimensions) WHERE active" in upgrade
    assert "ON disclosure_indexes (signature, status, lease_until)" in upgrade

    output.seek(0)
    output.truncate()
    with Operations.context(context):
        module.downgrade()
    downgrade = output.getvalue()
    assert re.findall(r"DROP TABLE (\w+)", downgrade) == list(reversed(tables))
    assert re.findall(r"DROP INDEX (\w+)", downgrade) == [
        "disclosure_indexes_work_idx",
        "disclosure_indexes_active_idx",
    ]
    assert downgrade.index("DROP INDEX disclosure_indexes_active_idx") < downgrade.index(
        "DROP TABLE disclosure_indexes"
    )
    assert "CREATE " not in downgrade and "ALTER TABLE " not in downgrade
