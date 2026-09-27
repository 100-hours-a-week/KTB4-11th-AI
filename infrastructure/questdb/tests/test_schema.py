import importlib.util
import pathlib
import re

import pytest
from market_collector.indicators import INDICATOR_FIELDS

REPO_ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / "alembic.ini").is_file()
)
QUESTDB_DIR = REPO_ROOT / "infrastructure" / "questdb"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, QUESTDB_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schema_mod = _load("questdb_schema", "schema.py")
apply_mod = _load("questdb_apply", "apply.py")


class _Column:
    """A cursor description entry: psycopg exposes the column name as ``.name``."""

    def __init__(self, name: str) -> None:
        self.name = name


BAR_TABLES = ["bars_1m", "bars_15m", "bars_1h", "bars_1d"]
THEME_TABLES = ["theme_snapshot", "theme_members"]
UNIVERSE_TABLES = ["universe_members"]

INDICATORS = list(INDICATOR_FIELDS)


def _statement_for(table: str) -> str:
    for statement in apply_mod.all_statements():
        if re.search(rf"CREATE TABLE IF NOT EXISTS\s+{table}\b", statement):
            return statement
    raise AssertionError(f"no CREATE TABLE for {table}")


def _dedup_keys(statement: str) -> str:
    match = re.search(r"DEDUP UPSERT KEYS\s*\(([^)]*)\)", statement)
    assert match is not None
    return match.group(1)


def _column_names(statement: str) -> set[str]:
    """The declared column names of a ``CREATE TABLE`` statement."""
    match = re.search(r"\((.*)\)\s*TIMESTAMP\(ts\)", statement, re.DOTALL)
    assert match is not None
    return {part.strip().split()[0] for part in match.group(1).split(",") if part.strip()}


def test_every_expected_table_is_declared():
    for table in BAR_TABLES + THEME_TABLES + UNIVERSE_TABLES:
        assert _statement_for(table)


def test_every_table_is_idempotent_walled_and_deduplicated():
    for table in BAR_TABLES + THEME_TABLES + UNIVERSE_TABLES:
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


def test_theme_members_records_membership_and_nothing_else():
    assert _column_names(_statement_for("theme_members")) == {
        "ts",
        "theme_code",
        "symbol",
        "stock_name",
    }


def test_universe_members_dedups_on_ts_index_code_and_symbol():
    keys = _dedup_keys(_statement_for("universe_members"))
    assert [k.strip() for k in keys.split(",")] == ["ts", "index_code", "symbol"]


def test_universe_members_indexes_both_index_code_and_symbol():
    statement = _statement_for("universe_members")
    assert re.search(r"\bindex_code\s+SYMBOL\s+INDEX\b", statement)
    assert re.search(r"\bsymbol\s+SYMBOL\s+INDEX\b", statement)


def test_universe_members_partitions_by_month():
    assert "PARTITION BY MONTH" in _statement_for("universe_members")


def test_candle_tables_carry_every_indicator_column():
    for table in BAR_TABLES:
        statement = _statement_for(table)
        for column in INDICATORS:
            assert re.search(rf"\b{column}\s+DOUBLE", statement), f"{table}.{column}"


def test_candle_columns_exactly_match_the_stored_indicator_fields():
    expected = (
        {"ts", "symbol", "session", "src"}  # metadata
        | {"open", "high", "low", "close", "volume", "trade_value"}  # OHLCV
        | set(INDICATOR_FIELDS)
    )
    for table in BAR_TABLES:
        assert _column_names(_statement_for(table)) == expected, table


def test_the_declared_columns_are_exactly_what_the_writer_emits():
    """The invariant generation does not remove."""
    from datetime import UTC, datetime

    from market_collector.store import CandleRow, Store

    emitted: set[str] = set()

    class RecordingSink:
        def row(self, table, *, symbols, columns, at):
            emitted.update(symbols)
            emitted.update(columns)
            emitted.add("ts")  # the designated timestamp arrives as `at`

        def flush(self):
            pass

    Store(RecordingSink()).write_candles(
        "1m",
        [
            CandleRow(
                ts=datetime(2026, 9, 27, tzinfo=UTC),
                symbol="005930",
                session="regular",
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=10,
                trade_value=100.0,
                indicators=dict.fromkeys(INDICATOR_FIELDS, 1.0),
                src="ws",
            )
        ],
    )

    declared = {name for name, _ in schema_mod.CANDLE_COLUMNS}
    assert emitted == declared


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
    assert not any("postgresql://" in st for st in apply_mod.all_statements())


def test_the_dsn_is_read_from_the_environment(monkeypatch):
    assert apply_mod.DSN_ENV == "KTB_QUESTDB_DSN"

    monkeypatch.delenv(apply_mod.DSN_ENV, raising=False)
    with pytest.raises(SystemExit):
        apply_mod.main()


def test_the_four_candle_tables_come_from_one_column_list():
    columns = [_column_names(statement) for statement in schema_mod.candle_tables()]

    assert len(columns) == 4
    assert all(names == columns[0] for names in columns[1:])


def test_the_generated_columns_are_in_the_order_the_list_declares():
    declared = [name for name, _ in schema_mod.CANDLE_COLUMNS]
    body = schema_mod.candle_tables()[0]

    positions = [body.index(f"    {name} ") for name in declared]

    assert positions == sorted(positions)


def test_every_stored_indicator_gets_a_double_column_without_being_listed_twice():
    declared = [name for name, type_ in schema_mod.CANDLE_COLUMNS if type_ == "DOUBLE"]

    for field in INDICATOR_FIELDS:
        assert declared.count(field) == 1, field


def test_print_writes_runnable_statements_instead_of_connecting(capsys, monkeypatch):
    monkeypatch.delenv(apply_mod.DSN_ENV, raising=False)
    monkeypatch.setattr(apply_mod.sys, "argv", ["apply.py", "--print"])

    apply_mod.main()

    printed = capsys.readouterr().out
    assert printed.count("CREATE TABLE IF NOT EXISTS") == len(apply_mod.all_statements())
    assert printed.rstrip().endswith(";")


def test_a_new_column_is_altered_into_a_table_that_already_exists():
    """CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so a column
    added to CANDLE_COLUMNS has to arrive by ALTER or it never lands."""
    declared = [name for name, _ in schema_mod.CANDLE_COLUMNS]
    altered: list[str] = []

    class FakeCursor:
        # the table exists but is two columns short
        description = tuple(_Column(name) for name in declared if name not in {"williams_r", "src"})

        def execute(self, statement):
            text = str(statement)
            if "ALTER TABLE" in text:
                altered.append(text)
            elif "LIMIT 0" in text and altered:
                # after the ALTERs the table reports the full shape
                self.description = tuple(_Column(name) for name in declared)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConnection:
        def __init__(self):
            self._cursor = FakeCursor()

        def cursor(self):
            return self._cursor

    added = apply_mod.add_missing_columns(FakeConnection(), "bars_1m")

    assert added == ["williams_r", "src"]
    assert any("ADD COLUMN williams_r DOUBLE" in statement for statement in altered)
    assert any("ADD COLUMN src SYMBOL" in statement for statement in altered)


def test_nothing_is_altered_when_the_table_already_matches():
    declared = [name for name, _ in schema_mod.CANDLE_COLUMNS]
    altered: list[str] = []

    class FakeCursor:
        description = tuple(_Column(name) for name in declared)

        def execute(self, statement):
            if "ALTER TABLE" in str(statement):
                altered.append(str(statement))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    assert apply_mod.add_missing_columns(FakeConnection(), "bars_1m") == []
    assert altered == []


def test_a_column_that_never_becomes_visible_raises_rather_than_reporting_success(
    monkeypatch,
):
    """The original defect was a silent success. A column that does not appear
    must fail loudly instead."""
    monkeypatch.setattr(apply_mod, "COLUMN_VISIBLE_TIMEOUT", 0.05)

    class FakeCursor:
        description = ()  # never reports any column

        def execute(self, statement):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    with pytest.raises(RuntimeError, match="did not appear"):
        apply_mod.add_missing_columns(FakeConnection(), "bars_1m")
