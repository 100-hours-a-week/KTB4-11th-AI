import importlib.util
import pathlib
import re

import pytest

REPO_ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / "alembic.ini").is_file()
)
QUESTDB_DIR = REPO_ROOT / "infrastructure" / "questdb"

_spec = importlib.util.spec_from_file_location("questdb_apply", QUESTDB_DIR / "apply.py")
assert _spec is not None and _spec.loader is not None
apply_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_mod)

BAR_TABLES = ["bars_1m", "bars_15m", "bars_1h", "bars_1d"]
THEME_TABLES = ["theme_snapshot", "theme_members"]
INDICATORS = [
    "rsi",
    "macd",
    "macd_signal",
    "macd_histogram",
    "stochastic_k",
    "stochastic_d",
    "roc",
    "williams_r",
]


def _all_sql() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in apply_mod.schema_files())


def _statement_for(table: str) -> str:
    for statement in apply_mod.statements(_all_sql()):
        if re.search(rf"CREATE TABLE IF NOT EXISTS\s+{table}\b", statement):
            return statement
    raise AssertionError(f"no CREATE TABLE for {table}")


def _dedup_keys(statement: str) -> str:
    match = re.search(r"DEDUP UPSERT KEYS\s*\(([^)]*)\)", statement)
    assert match is not None
    return match.group(1)


def test_every_expected_table_is_declared():
    for table in BAR_TABLES + THEME_TABLES:
        assert _statement_for(table)


def test_every_table_is_idempotent_walled_and_deduplicated():
    for table in BAR_TABLES + THEME_TABLES:
        statement = _statement_for(table)
        assert "IF NOT EXISTS" in statement
        assert "WAL" in statement
        assert "DEDUP UPSERT KEYS" in statement
        assert "PARTITION BY" in statement


def test_candle_tables_dedup_on_timestamp_and_symbol():
    for table in BAR_TABLES:
        statement = _statement_for(table)
        keys = _dedup_keys(statement)
        assert [k.strip() for k in keys.split(",")] == ["ts", "symbol"]


def test_theme_tables_dedup_on_their_own_keys():
    snapshot = _dedup_keys(_statement_for("theme_snapshot"))
    assert [k.strip() for k in snapshot.split(",")] == ["ts", "theme_code", "date_tp"]

    members = _dedup_keys(_statement_for("theme_members"))
    assert [k.strip() for k in members.split(",")] == ["ts", "theme_code", "symbol"]


def test_candle_tables_carry_every_indicator_column():
    for table in BAR_TABLES:
        statement = _statement_for(table)
        for column in INDICATORS:
            assert re.search(rf"\b{column}\s+DOUBLE", statement), f"{table}.{column}"


def test_partition_granularity_matches_candle_density():
    assert "PARTITION BY DAY" in _statement_for("bars_1m")
    assert "PARTITION BY MONTH" in _statement_for("bars_15m")
    assert "PARTITION BY MONTH" in _statement_for("bars_1h")
    assert "PARTITION BY YEAR" in _statement_for("bars_1d")


def test_statements_splits_and_drops_comments_and_blanks():
    sql = """
    -- a comment
    CREATE TABLE a (x INT);

    -- another
    CREATE TABLE b (y INT);
    """

    assert apply_mod.statements(sql) == ["CREATE TABLE a (x INT)", "CREATE TABLE b (y INT)"]


def test_no_dsn_is_baked_into_the_schema():
    assert "postgresql://" not in _all_sql()


def test_the_dsn_is_read_from_the_environment(monkeypatch):
    assert apply_mod.DSN_ENV == "KTB_QUESTDB_DSN"

    monkeypatch.delenv(apply_mod.DSN_ENV, raising=False)
    with pytest.raises(SystemExit):
        apply_mod.main()
