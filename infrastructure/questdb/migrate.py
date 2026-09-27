import os
from collections.abc import Sequence
from pathlib import Path

import questdb

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LEDGER_DDL = """CREATE TABLE IF NOT EXISTS questdb_migrations (
    name VARCHAR,
    applied_at TIMESTAMP
)"""


def migration_files(path: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(path.glob("*.sql"))


def applied_migrations(db) -> set[str]:
    with db.query("SELECT name FROM questdb_migrations") as result:
        return set(result.to_pandas()["name"].tolist())


def _statements(path: Path) -> list[str]:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    ]
    return [statement.strip() for statement in "\n".join(lines).split(";") if statement.strip()]


def apply_migrations(db, paths: Sequence[Path]) -> list[str]:
    applied = applied_migrations(db)
    completed = []
    for path in paths:
        if path.name in applied:
            continue
        for statement in _statements(path):
            db.execute(statement)
        db.execute("INSERT INTO questdb_migrations VALUES ($1, now())", [path.name])
        completed.append(path.name)
    return completed


def main() -> None:
    conf = os.environ.get("KTB_QUESTDB_CONF")
    if not conf:
        raise SystemExit("KTB_QUESTDB_CONF is not set")

    with questdb.connect(conf) as db:
        db.execute(LEDGER_DDL)
        completed = apply_migrations(db, migration_files())
    print(f"applied {len(completed)} QuestDB migration(s)")


if __name__ == "__main__":
    main()
