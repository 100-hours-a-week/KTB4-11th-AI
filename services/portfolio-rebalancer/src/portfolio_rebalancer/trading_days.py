from datetime import date, datetime, time
from functools import lru_cache
from typing import Any

# Sessions come from exchange-calendars, so public holidays such as Chuseok are real rather
# than approximated by weekdays.
EXCHANGE = "XKRX"
# KRX closes at 15:30, and polling runs on the hour, so 15:00 is the last pass before the
# close. That is where the ladder's final rung stops rather than running on.
CLOSE = time(15, 30)
MARKET_CUTOFF = time(15, 0)
# One rung per session, counted forward from the day the cycle began. The bands themselves
# live in order/reservations.py, which imports this module and so cannot be imported back.
LADDER_DAYS = 3


# Built once: constructing the calendar walks decades of holidays.
@lru_cache(maxsize=1)
def _calendar() -> Any:
    import exchange_calendars

    return exchange_calendars.get_calendar(EXCHANGE)


def is_open(day: date) -> bool:
    return bool(_calendar().is_session(day.isoformat()))


def ladder_day(started: date, now: datetime) -> int:
    """Which rung this pass is on: 1, 2, 3, or 0 once the ladder is spent.

    Counted forward in sessions from the day the cycle began, so a shut day is skipped
    rather than consumed. A cycle that starts on the Tuesday because the Monday was a
    holiday has its third day on the Thursday, and one that starts on a Wednesday has its
    third day on the Friday.

    The last rung ends at `MARKET_CUTOFF` rather than at midnight, because that is the
    last pass before the close. After it, and after the third session, there is no rung
    left: the order that is still outstanding stops being moved.
    """
    today = now.date()
    if today < started:
        return 0

    day = len(_sessions(started, today))
    if not 1 <= day <= LADDER_DAYS:
        return 0
    if day == LADDER_DAYS and now.timetz().replace(tzinfo=None) >= MARKET_CUTOFF:
        return 0
    return day


def _sessions(start: date, end: date) -> list[date]:
    if start > end:
        return []
    return [
        session.date()
        for session in _calendar().sessions_in_range(start.isoformat(), end.isoformat())
    ]
