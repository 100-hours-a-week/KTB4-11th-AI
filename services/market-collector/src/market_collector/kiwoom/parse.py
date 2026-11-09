from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ktb_core.krx import KST, MARKET_CLOSE, MARKET_OPEN

__all__ = [
    "DailyBar",
    "KiwoomParseError",
    "MinuteBar",
    "classify_session",
    "failure_fields",
    "parse_daily_bar",
    "parse_daily_ts",
    "parse_field",
    "parse_minute_bar",
    "parse_minute_ts",
    "parse_price",
    "parse_volume",
]


MAX_TEXT = 64
MAX_RESPONSE_FIELDS = 40


def _clip(value: Any) -> Any:
    return value[:MAX_TEXT] if isinstance(value, str) else value


class KiwoomParseError(ValueError):
    def __init__(self, field: str, data: Mapping[str, Any], **context: Any) -> None:
        self.field = field
        self.data = data
        self.context = context
        raw = repr(_clip(data[field])) if field in data else "missing"
        super().__init__(f"cannot parse {field}: {raw}")

    @property
    def fields(self) -> dict[str, Any]:
        missing = self.field not in self.data
        raw = self.data.get(self.field)
        scalars = [
            (key, _clip(value))
            for key, value in self.data.items()
            if value is None or isinstance(value, str | int | float | bool)
        ]
        return {
            **self.context,
            "field": self.field,
            "raw_value": _clip(raw),
            "raw_type": "missing" if missing else type(raw).__name__,
            "error_type": type(self.__cause__).__name__ if self.__cause__ else None,
            "response": dict(scalars[:MAX_RESPONSE_FIELDS]),
        }


def failure_fields(error: BaseException) -> dict[str, Any]:
    return error.fields if isinstance(error, KiwoomParseError) else {}


def parse_field[T](
    data: Mapping[str, Any], field: str, convert: Callable[[Any], T], **context: Any
) -> T:
    try:
        return convert(data[field])
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise KiwoomParseError(field, data, **context) from error


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
    if MARKET_OPEN <= local <= MARKET_CLOSE:
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


@dataclass(frozen=True)
class DailyBar:
    ts: datetime
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int


def parse_minute_bar(row: dict[str, str]) -> MinuteBar:
    ts = parse_field(row, "cntr_tm", parse_minute_ts)
    return MinuteBar(
        ts=ts,
        session=classify_session(ts),
        open=parse_field(row, "open_pric", parse_price),
        high=parse_field(row, "high_pric", parse_price),
        low=parse_field(row, "low_pric", parse_price),
        close=parse_field(row, "cur_prc", parse_price),
        volume=parse_field(row, "trde_qty", parse_volume),
    )


def parse_daily_bar(row: dict[str, str]) -> DailyBar:
    ts = parse_field(row, "dt", parse_daily_ts)
    return DailyBar(
        ts=ts,
        session="regular",
        open=parse_field(row, "open_pric", parse_price),
        high=parse_field(row, "high_pric", parse_price),
        low=parse_field(row, "low_pric", parse_price),
        close=parse_field(row, "cur_prc", parse_price),
        volume=parse_field(row, "trde_qty", parse_volume),
    )
