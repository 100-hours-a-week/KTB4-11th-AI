import importlib.util
import pathlib
import re

import pytest
from market_collector.indicators import COMMENT_FIELDS, INDICATOR_FIELDS

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

# Derived from market_collector.indicators rather than hand-listed: Task 3's
# convention is that new indicators are added to ktb_market_analyzer one at a
# time, and a hand-maintained copy here would silently drift the day the
# first one lands — the DDL would never declare the new column, ILP would
# auto-create it untyped and unindexed (or the server would reject the row),
# and this file would still pass because it was only checking itself.
INDICATORS = list(INDICATOR_FIELDS)

# Every indicator except macd_signal carries a verdict beside its value. The
# exception is deliberate: macd_signal is a smoothed copy of the MACD line, so
# every event involving it is already reported on macd_histogram.
COMMENTED = list(COMMENT_FIELDS)


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


def _column_names(statement: str) -> set[str]:
    """The declared column names of a ``CREATE TABLE`` statement.

    Parses the ``(col type, col type, ...)`` block between the table name
    and ``TIMESTAMP(ts)``, taking each entry's first token as the column
    name — robust to the type and any trailing modifier (``SYMBOL INDEX``).
    """
    match = re.search(r"\((.*)\)\s*TIMESTAMP\(ts\)", statement, re.DOTALL)
    assert match is not None
    return {part.strip().split()[0] for part in match.group(1).split(",") if part.strip()}


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


def test_candle_tables_carry_a_verdict_column_beside_each_indicator():
    for table in BAR_TABLES:
        statement = _statement_for(table)
        for column in COMMENTED:
            assert re.search(rf"\b{column}_comment\s+SYMBOL", statement), f"{table}.{column}"


def test_macd_signal_has_no_verdict_column():
    # A verdict here would duplicate what macd_histogram already reports, and a
    # column nothing ever writes reads as a gap in the collector rather than a
    # decision.
    for table in BAR_TABLES:
        assert "macd_signal_comment" not in _statement_for(table), table


def test_candle_columns_exactly_match_the_analyzers_indicator_and_comment_fields():
    # Pins the DDL's column set to the code's own field lists rather than to
    # anything hand-maintained in this file, so the first indicator Task 3's
    # convention adds shows up here as a failing test instead of a silently
    # untyped, unindexed column (or a rejected row) in production.
    expected = (
        {"ts", "symbol", "session", "src"}  # metadata
        | {"open", "high", "low", "close", "volume", "trade_value"}  # OHLCV
        | set(INDICATOR_FIELDS)
        | {f"{field}_comment" for field in COMMENT_FIELDS}
    )
    for table in BAR_TABLES:
        assert _column_names(_statement_for(table)) == expected, table


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
