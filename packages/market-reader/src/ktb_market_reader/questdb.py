"""Read candles, themes and index membership out of QuestDB over the Postgres wire.

Reads only. Writing is the market-collector service's job and goes over a
different protocol (ILP), so nothing here opens a writer.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import LiteralString, cast

__all__ = [
    "TIMEFRAME_TABLES",
    "Candle",
    "EmptyThemeSnapshotError",
    "EmptyUniverseError",
    "Theme",
    "latest_members",
    "read_regular_candles",
    "read_symbol_themes",
    "read_themes",
]

TIMEFRAME_TABLES: dict[str, str] = {
    "1m": "bars_1m",
    "15m": "bars_15m",
    "1h": "bars_1h",
    "1d": "bars_1d",
}

THEME_SNAPSHOT_TABLE = "theme_snapshot"
THEME_MEMBERS_TABLE = "theme_members"
UNIVERSE_MEMBERS_TABLE = "universe_members"

# A candle table's own columns. Everything else it carries is a stored indicator,
# which is how a read picks the indicators up without being told their names --
# add a column to the table and it appears here with no change to this module.
BASE_CANDLE_COLUMNS = frozenset(
    {
        "ts",
        "symbol",
        "session",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_value",
        "src",
    }
)


class EmptyThemeSnapshotError(RuntimeError):
    pass


class EmptyUniverseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candle:
    ts: datetime
    high: float
    low: float
    close: float
    indicators: dict[str, float | None]


@dataclass(frozen=True)
class Theme:
    theme_code: str
    theme_name: str
    date_tp: int
    dt_prft_rt: float | None
    change_rate: float | None
    stock_count: int
    rising_count: int
    falling_count: int
    main_stocks: str
    members: tuple[tuple[str, str], ...]


def read_regular_candles(
    dsn: str,
    timeframe: str,
    symbol: str,
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> list[Candle]:
    """Regular-session candles for one symbol, oldest first, with stored indicators.

    ``since`` is inclusive. ``limit`` selects the newest rows while keeping the
    oldest-first result, and must be positive. Ask for generous history: RSI
    needs 14 prior candles and MACD 33, so a narrow window returns rows whose
    indicator values are all null.

    Which indicator columns come back is whatever the table holds -- this reads
    the column names off the cursor rather than carrying a list of its own.
    """
    if timeframe not in TIMEFRAME_TABLES:
        raise KeyError(f"unknown timeframe: {timeframe}")
    if limit is not None and limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    import psycopg

    table = TIMEFRAME_TABLES[timeframe]
    since_clause = " AND ts >= %s" if since is not None else ""
    order_clause = "ORDER BY ts DESC LIMIT %s" if limit is not None else "ORDER BY ts ASC"
    query = cast(
        LiteralString,
        f"SELECT * FROM {table} "
        f"WHERE symbol = %s AND session = 'regular'{since_clause} {order_clause}",
    )
    params: tuple[object, ...] = (symbol,)
    if since is not None:
        params += (since,)
    if limit is not None:
        params += (limit,)

    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(query, params)
        names = [column.name for column in cursor.description or ()]
        at = {name: position for position, name in enumerate(names)}
        indicators = [name for name in names if name not in BASE_CANDLE_COLUMNS]
        rows = [
            Candle(
                ts=row[at["ts"]],
                high=row[at["high"]],
                low=row[at["low"]],
                close=row[at["close"]],
                indicators={name: row[at[name]] for name in indicators},
            )
            for row in cursor.fetchall()
        ]

    if limit is not None:
        rows.reverse()
    return rows


def read_themes(dsn: str, date_tp: int, *, limit: int | None = None) -> list[Theme]:
    """The latest theme snapshot for one period, each theme with its constituents.

    ``date_tp`` selects the period because Kiwoom reports different figures per
    period for the same theme. Ordered by ``dt_prft_rt`` descending with unknown
    values last.

    ``stock_count``, ``rising_count``, ``falling_count`` and ``dt_prft_rt`` are
    Kiwoom's figures over a theme's whole market-wide membership, while
    ``members`` holds only the KOSPI 200 constituents this service stores. The
    two do not match and must not be combined into a ratio.
    """
    if limit is not None and limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    import psycopg

    snapshot_query = cast(
        LiteralString,
        f"SELECT theme_code, theme_name, date_tp, dt_prft_rt, change_rate, stock_count, "
        f"rising_count, falling_count, main_stocks FROM {THEME_SNAPSHOT_TABLE} "
        f"WHERE date_tp = %s AND ts = "
        f"(SELECT max(ts) FROM {THEME_SNAPSHOT_TABLE} WHERE date_tp = %s)",
    )
    members_query = cast(
        LiteralString,
        f"SELECT theme_code, symbol, stock_name FROM {THEME_MEMBERS_TABLE} "
        f"WHERE ts = (SELECT max(ts) FROM {THEME_MEMBERS_TABLE}) ORDER BY symbol",
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(snapshot_query, (date_tp, date_tp))
        snapshots = cursor.fetchall()
        cursor.execute(members_query)
        member_rows = cursor.fetchall()

    if not snapshots:
        raise EmptyThemeSnapshotError(
            f"no theme_snapshot rows for date_tp={date_tp}; run `market-collector themes` first"
        )

    by_theme: dict[str, list[tuple[str, str]]] = {}
    for theme_code, symbol, stock_name in member_rows:
        by_theme.setdefault(theme_code, []).append((symbol, stock_name))

    themes = [
        Theme(
            theme_code=row[0],
            theme_name=row[1],
            date_tp=row[2],
            dt_prft_rt=row[3],
            change_rate=row[4],
            stock_count=row[5],
            rising_count=row[6],
            falling_count=row[7],
            main_stocks=row[8],
            members=tuple(by_theme.get(row[0], ())),
        )
        for row in snapshots
    ]
    themes.sort(key=lambda t: (t.dt_prft_rt is None, -(t.dt_prft_rt or 0.0), t.theme_code))
    return themes if limit is None else themes[:limit]


def read_symbol_themes(dsn: str, symbol: str, date_tp: int) -> list[Theme]:
    """The themes one symbol belongs to, highest-rated first, each with its members.

    A symbol in no theme returns an empty list; only a missing snapshot raises.
    """
    return [theme for theme in read_themes(dsn, date_tp) if symbol in dict(theme.members)]


def latest_members(dsn: str, index_code: str) -> frozenset[str]:
    """The symbols in the most recent index snapshot."""
    import psycopg

    query = cast(
        LiteralString,
        f"SELECT symbol FROM {UNIVERSE_MEMBERS_TABLE} WHERE index_code = %s "
        f"AND ts = (SELECT max(ts) FROM {UNIVERSE_MEMBERS_TABLE} WHERE index_code = %s)",
    )
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(query, (index_code, index_code))
        symbols = frozenset(row[0] for row in cursor.fetchall())

    if not symbols:
        raise EmptyUniverseError(
            f"no universe_members snapshot for index_code={index_code!r}; "
            "run `market-collector universe` first"
        )
    return symbols
