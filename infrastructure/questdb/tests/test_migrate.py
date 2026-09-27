import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[3]
MIGRATE_PATH = REPO_ROOT / "infrastructure" / "questdb" / "migrate.py"


def _load_migrate():
    spec = importlib.util.spec_from_file_location("questdb_migrate", MIGRATE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeFrame:
    def __init__(self, names):
        self._names = names

    def __getitem__(self, name):
        assert name == "name"
        return self

    def tolist(self):
        return self._names


class FakeResult:
    def __init__(self, names):
        self._names = names

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def to_pandas(self):
        return FakeFrame(self._names)


class FakeDatabase:
    def __init__(self, applied=(), fail_on=None):
        self.applied = list(applied)
        self.fail_on = fail_on
        self.executed = []

    def query(self, sql):
        assert sql == "SELECT name FROM questdb_migrations"
        return FakeResult(self.applied)

    def execute(self, sql, binds=None):
        if sql == self.fail_on:
            raise RuntimeError("migration failed")
        self.executed.append((sql, binds))


def test_migration_files_are_sorted(tmp_path):
    migrate = _load_migrate()
    (tmp_path / "001_second.sql").write_text("SELECT 2;", encoding="utf-8")
    (tmp_path / "000_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("SELECT 0;", encoding="utf-8")

    assert [path.name for path in migrate.migration_files(tmp_path)] == [
        "000_first.sql",
        "001_second.sql",
    ]


def test_apply_migrations_skips_applied_files_and_records_after_all_statements(tmp_path):
    migrate = _load_migrate()
    old = tmp_path / "000_old.sql"
    new = tmp_path / "001_new.sql"
    old.write_text("SELECT 'old';", encoding="utf-8")
    new.write_text("SELECT 1; SELECT 2;", encoding="utf-8")
    db = FakeDatabase(applied=[old.name])

    assert migrate.apply_migrations(db, [old, new]) == [new.name]
    assert db.executed == [
        ("SELECT 1", None),
        ("SELECT 2", None),
        ("INSERT INTO questdb_migrations VALUES ($1, now())", [new.name]),
    ]


def test_failed_migration_is_not_recorded(tmp_path):
    migrate = _load_migrate()
    path = tmp_path / "001_broken.sql"
    path.write_text("SELECT 1; SELECT broken;", encoding="utf-8")
    db = FakeDatabase(fail_on="SELECT broken")

    with pytest.raises(RuntimeError, match="migration failed"):
        migrate.apply_migrations(db, [path])

    assert db.executed == [("SELECT 1", None)]


def test_main_requires_connection_configuration(monkeypatch):
    migrate = _load_migrate()
    monkeypatch.delenv("KTB_QUESTDB_CONF", raising=False)

    with pytest.raises(SystemExit, match="KTB_QUESTDB_CONF is not set"):
        migrate.main()


def test_main_connects_and_applies_discovered_migrations(monkeypatch):
    migrate = _load_migrate()
    db = FakeDatabase()

    class Connection:
        def __enter__(self):
            return db

        def __exit__(self, *args):
            pass

    seen = []
    monkeypatch.setenv("KTB_QUESTDB_CONF", "ws::addr=questdb:9000;")
    monkeypatch.setattr(migrate.questdb, "connect", lambda conf: seen.append(conf) or Connection())
    monkeypatch.setattr(migrate, "migration_files", lambda: [])

    migrate.main()

    assert seen == ["ws::addr=questdb:9000;"]
    assert db.executed == [(migrate.LEDGER_DDL, None)]


def test_market_data_migration_defines_archive_schema():
    migration_path = (
        REPO_ROOT / "infrastructure" / "questdb" / "migrations" / "0001_market_data.sql"
    )
    sql = migration_path.read_text(encoding="utf-8")
    bars = sql[: sql.index("CREATE VIEW")]

    assert "timeframe VARCHAR" in bars
    for column in ("open DOUBLE", "high DOUBLE", "low DOUBLE", "close DOUBLE", "volume LONG"):
        assert column in bars

    for timeframe in ("1m", "1d"):
        assert (
            f"CREATE VIEW IF NOT EXISTS bars_{timeframe} AS "
            f"(SELECT * FROM bars WHERE timeframe = '{timeframe}');"
        ) in sql
    for timeframe in ("15m", "1h"):
        assert f"CREATE MATERIALIZED VIEW IF NOT EXISTS bars_{timeframe}" in sql
        view = sql[sql.index(f"bars_{timeframe}") :]
        view = view[: view.index(";") + 1]
        assert "FROM bars" in view
        assert "first(open)" in view
        assert "max(high)" in view
        assert "min(low)" in view
        assert "last(close)" in view
        assert "sum(volume)" in view
        assert "WHERE timeframe = '1m'" in view
        assert f"SAMPLE BY {timeframe}" in view

    assert "CREATE TABLE IF NOT EXISTS universe_members" in sql
    assert "theme_snapshot" not in sql
    assert "theme_members" not in sql
