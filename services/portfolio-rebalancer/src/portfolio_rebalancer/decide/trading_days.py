"""Which days the Korean exchange opens, and how many a rebalance has left.

The weekly judgement is the deadline: a cycle has the trading days that remain in the
week it began, and selling and buying share them. Five of them means two days of selling
leaves three for buying; a Chuseok week with three means far less.

Sessions come from `exchange-calendars`' XKRX, so public holidays are real rather than
approximated by weekdays.
"""

from datetime import date, datetime, time
from functools import lru_cache
from typing import Any

__all__ = ["CLOSE", "MARKET_CUTOFF", "days_left", "is_open", "week_deadline"]

EXCHANGE = "XKRX"
# KRX closes at 15:30. On the last day the market order has to be in before that, so the
# final hour switches to market: polling at least hourly puts a pass inside it.
CLOSE = time(15, 30)
MARKET_CUTOFF = time(14, 30)


@lru_cache(maxsize=1)
def _calendar() -> Any:
    """Built once. Constructing it walks decades of holidays, so it is not cheap."""
    import exchange_calendars

    return exchange_calendars.get_calendar(EXCHANGE)


def is_open(day: date) -> bool:
    return bool(_calendar().is_session(day.isoformat()))


def week_deadline(started: date) -> date:
    """The last session of the week the cycle began in.

    The next judgement lands the following week, so an order still working then would be
    acting on a portfolio that has been replaced.
    """
    monday = started.fromordinal(started.toordinal() - started.weekday())
    sunday = monday.fromordinal(monday.toordinal() + 6)
    sessions = _sessions(monday, sunday)
    return sessions[-1] if sessions else started


def days_left(started: date, now: datetime) -> int:
    """Sessions from now to the deadline, counting today.

    A day the market does not open takes nothing off the budget, and a cycle past its
    deadline has nothing left -- which is the market rung.

    On the last day the budget runs out at `MARKET_CUTOFF` rather than at midnight, so
    the market order goes in before the close instead of the morning after.
    """
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
