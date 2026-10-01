from datetime import date, datetime, time
from functools import lru_cache
from typing import Any

# Sessions come from exchange-calendars, so public holidays such as Chuseok are real rather
# than approximated by weekdays.
EXCHANGE = "XKRX"
# A cycle runs three sessions: day 1 places the orders, days 2 and 3 tighten what has not
# filled, and whatever is still unfilled at 15:00 on day 3 is blocked. KRX closes at 15:30,
# and polling runs on the hour, so 15:00 is the last pass before the close.
CYCLE_DAYS = 3
CLOSE = time(15, 30)
BLOCK_CUTOFF = time(15, 0)


# Built once: constructing the calendar walks decades of holidays.
@lru_cache(maxsize=1)
def _calendar() -> Any:
    import exchange_calendars

    return exchange_calendars.get_calendar(EXCHANGE)


def is_open(day: date) -> bool:
    return bool(_calendar().is_session(day.isoformat()))


def cycle_day(started: date, today: date) -> int:
    # Counted in sessions, so a holiday is skipped rather than spent: a cycle that begins on a
    # Tuesday after a Monday holiday has its day 3 on Thursday, and one that begins on a
    # Wednesday has it on Friday.
    return len(_sessions(started, today))


def is_blocked(day: int, now: datetime) -> bool:
    if day > CYCLE_DAYS:
        return True
    return day == CYCLE_DAYS and now.timetz().replace(tzinfo=None) >= BLOCK_CUTOFF


def _sessions(start: date, end: date) -> list[date]:
    if start > end:
        return []
    return [
        session.date()
        for session in _calendar().sessions_in_range(start.isoformat(), end.isoformat())
    ]
