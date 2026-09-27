"""The candle tables' shape, in one place."""

from market_collector.indicators import INDICATOR_FIELDS

__all__ = ["CANDLE_COLUMNS", "CANDLE_PARTITIONS", "candle_tables"]

CANDLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ts", "TIMESTAMP"),
    ("symbol", "SYMBOL INDEX"),
    ("session", "SYMBOL"),  # 'regular' | 'extended'
    ("open", "DOUBLE"),
    ("high", "DOUBLE"),
    ("low", "DOUBLE"),
    ("close", "DOUBLE"),
    ("volume", "LONG"),
    ("trade_value", "DOUBLE"),  # daily only; null elsewhere
    *((field, "DOUBLE") for field in INDICATOR_FIELDS),
    ("src", "SYMBOL"),  # 'rest' | 'ws' -- which path wrote this row
)

CANDLE_PARTITIONS: dict[str, str] = {
    "bars_1m": "DAY",
    "bars_15m": "MONTH",
    "bars_1h": "MONTH",
    "bars_1d": "YEAR",
}

_DEDUP_KEYS = "ts, symbol"


def candle_tables() -> list[str]:
    """The four ``CREATE TABLE IF NOT EXISTS`` statements, built from one list."""
    body = ",\n".join(f"    {name} {type_}" for name, type_ in CANDLE_COLUMNS)
    return [
        f"CREATE TABLE IF NOT EXISTS {table} (\n{body}\n)"
        f" TIMESTAMP(ts) PARTITION BY {partition}"
        f" WAL DEDUP UPSERT KEYS({_DEDUP_KEYS})"
        for table, partition in CANDLE_PARTITIONS.items()
    ]
