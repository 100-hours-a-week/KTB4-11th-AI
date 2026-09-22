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
"""

import os
import pathlib
import sys

DSN_ENV = "KTB_QUESTDB_DSN"
SCHEMA_DIR = pathlib.Path(__file__).parent / "schema"


def schema_files() -> list[pathlib.Path]:
    return sorted(SCHEMA_DIR.glob("*.sql"))


def statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return [chunk.strip() for chunk in "\n".join(lines).split(";") if chunk.strip()]


def apply(dsn: str) -> int:
    import typing

    import psycopg
    from psycopg import sql

    executed = 0
    with psycopg.connect(dsn, autocommit=True) as connection:
        for path in schema_files():
            for statement in statements(path.read_text(encoding="utf-8")):
                # Statements come from our own schema files, not user input;
                # psycopg's stub requires LiteralString, which a runtime-read
                # string can never satisfy.
                query = sql.SQL(typing.cast(typing.LiteralString, statement))
                with connection.cursor() as cursor:
                    cursor.execute(query)
                executed += 1
    return executed


def main() -> None:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        raise SystemExit(
            f"{DSN_ENV} is not set. Example: {DSN_ENV}=postgresql://admin:quest@localhost:8812/qdb"
        )
    count = apply(dsn)
    print(f"applied {count} statements from {len(schema_files())} files")


if __name__ == "__main__":
    sys.exit(main())
