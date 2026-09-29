from datetime import date, datetime, time
from functools import lru_cache
from typing import Any

# Sessions come from exchange-calendars, so public holidays such as Chuseok are real rather
# than approximated by weekdays.
EXCHANGE = "XKRX"
# KRX closes at 15:30, and polling starts at 09:00 on the hour, so 15:00 is the last pass
# before the close. The final band's order goes at market there rather than the morning
# after.
CLOSE = time(15, 30)
MARKET_CUTOFF = time(15, 0)


# Built once: constructing the calendar walks decades of holidays.
@lru_cache(maxsize=1)
def _calendar() -> Any:
    import exchange_calendars

    return exchange_calendars.get_calendar(EXCHANGE)


def is_open(day: date) -> bool:
    return bool(_calendar().is_session(day.isoformat()))


def week_deadline(started: date) -> date:
    # The next judgement lands the following week, so an order still working then would be
    # acting on a portfolio that has been replaced.
    monday = started.fromordinal(started.toordinal() - started.weekday())
    sunday = monday.fromordinal(monday.toordinal() + 6)
    sessions = _sessions(monday, sunday)
    return sessions[-1] if sessions else started


def days_left(started: date, now: datetime) -> int:
    # Selling and buying share the sessions left in the week the cycle began, counting today.
    # A cycle past its deadline has none, which is the market rung, and on the last day the
    # budget runs out at MARKET_CUTOFF so the market order goes in before the close.
    today = now.date()
    deadline = week_deadline(started)
    if today > deadline:
        return 0

    left = len(_sessions(max(today, started), deadline))
    if left == 1 and now.timetz().replace(tzinfo=None) >= MARKET_CUTOFF:
        return 0
    return left


def _sessions(start: date, end: date) -> list[date]:
    if start > end:
        return []
    return [
        session.date()
        for session in _calendar().sessions_in_range(start.isoformat(), end.isoformat())
    ]
