"""Turn Kiwoom's chart rows into typed candles.

Two quirks drive this module. Minute-chart prices arrive with a sign prefix
(``"+277500"``) while daily prices do not, and the close is named ``cur_prc`` in
both, which reads as "current price". Timestamps arrive in KST; everything
stored is UTC.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")

REGULAR_OPEN = time(9, 0)
REGULAR_CLOSE = time(15, 30)

__all__ = [
    "DailyBar",
    "MinuteBar",
    "classify_session",
    "parse_daily_bar",
    "parse_daily_ts",
    "parse_minute_bar",
    "parse_minute_ts",
    "parse_price",
    "parse_volume",
]


def parse_price(raw: str) -> float:
    text = raw.strip().lstrip("+-")
    if not text:
        raise ValueError(f"not a price: {raw!r}")
    return float(text)


def parse_volume(raw: str) -> int:
    text = raw.strip().lstrip("+-")
    if not text:
        raise ValueError(f"not a volume: {raw!r}")
    return int(text)


def parse_minute_ts(raw: str) -> datetime:
    naive = datetime.strptime(raw.strip(), "%Y%m%d%H%M%S")
    return naive.replace(tzinfo=KST).astimezone(UTC)


def parse_daily_ts(raw: str) -> datetime:
    naive = datetime.strptime(raw.strip(), "%Y%m%d")
    return naive.replace(tzinfo=UTC)


def classify_session(ts_utc: datetime) -> str:
    local = ts_utc.astimezone(KST).time()
    if REGULAR_OPEN <= local <= REGULAR_CLOSE:
        return "regular"
    return "extended"


@dataclass(frozen=True)
class MinuteBar:
    ts: datetime
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_value: float | None = None


@dataclass(frozen=True)
class DailyBar:
    ts: datetime
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    trade_value: float | None = None


def parse_minute_bar(row: dict[str, str]) -> MinuteBar:
    ts = parse_minute_ts(row["cntr_tm"])
    return MinuteBar(
        ts=ts,
        session=classify_session(ts),
        open=parse_price(row["open_pric"]),
        high=parse_price(row["high_pric"]),
        low=parse_price(row["low_pric"]),
        close=parse_price(row["cur_prc"]),
        volume=parse_volume(row["trde_qty"]),
    )


def parse_daily_bar(row: dict[str, str]) -> DailyBar:
    ts = parse_daily_ts(row["dt"])
    return DailyBar(
        ts=ts,
        session="regular",
        open=parse_price(row["open_pric"]),
        high=parse_price(row["high_pric"]),
        low=parse_price(row["low_pric"]),
        close=parse_price(row["cur_prc"]),
        volume=parse_volume(row["trde_qty"]),
        trade_value=parse_price(row["trde_prica"]),
    )
