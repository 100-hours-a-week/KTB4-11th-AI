from datetime import date, time
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(15, 30)

KRX_HOLIDAYS = frozenset(
    {
        date(2026, 10, 5),
        date(2026, 10, 9),
        date(2026, 12, 25),
        date(2026, 12, 31),
        date(2027, 1, 1),
        date(2027, 2, 8),
        date(2027, 2, 9),
        date(2027, 3, 1),
        date(2027, 5, 5),
        date(2027, 5, 13),
        date(2027, 8, 16),
        date(2027, 9, 14),
        date(2027, 9, 15),
        date(2027, 9, 16),
    }
)
COVERED_THROUGH = date(2027, 10, 1)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in KRX_HOLIDAYS
