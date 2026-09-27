"""Apply the QuestDB schema.

This is an operational asset, not library code — nothing imports it. It lives
here rather than inside market-collector so that no service can reach a
CREATE TABLE at boot. Services read and write rows; nothing mutates schema as
a side effect of starting.

QuestDB has no Alembic dialect worth targeting and narrow ALTER support, so
the schema is idempotent CREATE TABLE IF NOT EXISTS rather than versioned
migrations.

Usage: KTB_QUESTDB_DSN=postgresql://admin:quest@localhost:8812/qdb \\
           uv run --group migrations python infrastructure/questdb/apply.py

Pass ``--print`` to write the CREATE statements to stdout instead of running
them. ``--print`` cannot show the ALTER statements: which columns are missing
depends on the database being applied to.
"""

import os
import pathlib
import sys

DSN_ENV = "KTB_QUESTDB_DSN"
SCHEMA_DIR = pathlib.Path(__file__).parent / "schema"

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from schema import CANDLE_COLUMNS, CANDLE_PARTITIONS, candle_tables  # noqa: E402


def schema_files() -> list[pathlib.Path]:
    return sorted(SCHEMA_DIR.glob("*.sql"))


def all_statements() -> list[str]:
    """Everything to apply: the generated candle tables, then the .sql files."""
    from_files = [
        statement
        for path in schema_files()
        for statement in statements(path.read_text(encoding="utf-8"))
    ]
    return candle_tables() + from_files


def statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [chunk.strip() for chunk in "\n".join(lines).split(";") if chunk.strip()]


# A WAL table applies a column addition asynchronously, so a read straight
# afterwards can still show the old shape. Measured at about 0.3s.
COLUMN_VISIBLE_TIMEOUT = 5.0


def _run(connection, statement: str) -> None:
    import typing

    from psycopg import sql

    query = sql.SQL(typing.cast(typing.LiteralString, statement))
    with connection.cursor() as cursor:
        cursor.execute(query)


def _columns(connection, table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT * FROM {table} LIMIT 0")  # noqa: S608 — our own table name
        return {column.name for column in cursor.description or ()}


def add_missing_columns(connection, table: str) -> list[str]:
    """Add the declared candle columns this table does not have yet.

    CREATE TABLE IF NOT EXISTS silently does nothing to a table that already
    exists, so a column added to CANDLE_COLUMNS would never reach a live
    database without this. ADD COLUMN IF NOT EXISTS is not idempotent on WAL
    tables -- repeating it raises -- so only the genuinely missing columns are
    altered.
    """
    import time

    have = _columns(connection, table)
    missing = [(name, type_) for name, type_ in CANDLE_COLUMNS if name not in have]
    for name, type_ in missing:
        _run(connection, f"ALTER TABLE {table} ADD COLUMN {name} {type_}")

    if missing:
        deadline = time.monotonic() + COLUMN_VISIBLE_TIMEOUT
        wanted = {name for name, _ in missing}
        while not wanted <= _columns(connection, table):
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"{table}: added {sorted(wanted)} but the columns did not appear "
                    f"within {COLUMN_VISIBLE_TIMEOUT}s"
                )
            time.sleep(0.1)
    return [name for name, _ in missing]


def apply(dsn: str) -> tuple[int, dict[str, list[str]]]:
    """Run every statement, then reconcile the candle tables' columns."""
    import psycopg

    executed = 0
    added: dict[str, list[str]] = {}
    with psycopg.connect(dsn, autocommit=True) as connection:
        for statement in all_statements():
            _run(connection, statement)
            executed += 1
        for table in CANDLE_PARTITIONS:
            names = add_missing_columns(connection, table)
            if names:
                added[table] = names
    return executed, added


def main() -> None:
    if "--print" in sys.argv[1:]:
        print(";\n\n".join(all_statements()) + ";")
        return

    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        raise SystemExit(
            f"{DSN_ENV} is not set. Example: {DSN_ENV}=postgresql://admin:quest@localhost:8812/qdb"
        )
    count, added = apply(dsn)
    print(
        f"applied {count} statements: {len(candle_tables())} generated candle tables "
        f"and the rest from {len(schema_files())} files"
    )
    for table, names in added.items():
        print(f"added to {table}: {', '.join(names)}")
    if not added:
        print("no columns were missing from the candle tables")


if __name__ == "__main__":
    sys.exit(main())
