"""The exchange calendar, against real Korean holidays."""

from datetime import date, datetime, time, timedelta, timezone

import pytest
from portfolio_rebalancer.order.reservations import PRICE_BANDS, band_for
from portfolio_rebalancer.trading_days import (
    BLOCK_CUTOFF,
    CLOSE,
    CYCLE_DAYS,
    cycle_day,
    is_blocked,
    is_open,
)

KST = timezone(timedelta(hours=9))

# 2026-09-28 is a full week. 2026-10-05 is a holiday Monday (the substitute for 개천절),
# and 2026-10-09 is 한글날 on the Friday.
MONDAY = date(2026, 9, 28)
WEDNESDAY = date(2026, 9, 30)
THURSDAY = date(2026, 10, 1)
FRIDAY = date(2026, 10, 2)
HOLIDAY_MONDAY = date(2026, 10, 5)


def at(day: date, hour=11, minute=0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 9, 28), True),
        (date(2026, 10, 2), True),
        (date(2026, 10, 3), False),
        (date(2026, 10, 5), False),
        (date(2026, 10, 9), False),
    ],
)
def test_the_calendar_knows_korean_holidays(day, expected):
    """Weekdays were an approximation; 한글날 and 개천절 are not weekends."""
    assert is_open(day) is expected


def test_a_cycle_started_on_monday_runs_monday_to_wednesday():
    days = [cycle_day(MONDAY, MONDAY + timedelta(days=n)) for n in range(3)]

    assert days == [1, 2, 3]


def test_a_cycle_after_a_holiday_monday_runs_tuesday_to_thursday():
    """The holiday is skipped, not spent: day 1 is Tuesday and day 3 is Thursday."""
    tuesday = HOLIDAY_MONDAY + timedelta(days=1)

    assert cycle_day(tuesday, tuesday) == 1
    assert cycle_day(tuesday, tuesday + timedelta(days=1)) == 2
    assert cycle_day(tuesday, tuesday + timedelta(days=2)) == 3


def test_an_account_first_seen_on_wednesday_has_its_day_3_on_friday():
    assert cycle_day(WEDNESDAY, WEDNESDAY) == 1
    assert cycle_day(WEDNESDAY, THURSDAY) == 2
    assert cycle_day(WEDNESDAY, FRIDAY) == 3


def test_a_weekend_and_a_holiday_are_both_skipped():
    """Started on Thursday: Friday is day 2, and the next session after the weekend and the
    holiday Monday is day 3."""
    tuesday = HOLIDAY_MONDAY + timedelta(days=1)

    assert cycle_day(THURSDAY, FRIDAY) == 2
    assert cycle_day(THURSDAY, HOLIDAY_MONDAY) == 2
    assert cycle_day(THURSDAY, tuesday) == 3


def test_each_day_has_its_own_band():
    assert [band_for(day) for day in range(1, CYCLE_DAYS + 1)] == [0.05, 0.03, 0.01]
    assert list(PRICE_BANDS) == [0.05, 0.03, 0.01]


def test_there_is_no_band_outside_the_cycle():
    assert band_for(0) is None
    assert band_for(CYCLE_DAYS + 1) is None


def test_day_3_is_blocked_from_15_00():
    """KRX closes at 15:30, so 15:00 is the last pass before the close."""
    assert not is_blocked(3, at(WEDNESDAY, 14, 0))
    assert not is_blocked(3, at(WEDNESDAY, 14, 59))
    assert is_blocked(3, at(WEDNESDAY, 15, 0))
    assert is_blocked(3, at(WEDNESDAY, 15, 29))


def test_the_cutoff_does_not_block_an_earlier_day():
    assert not is_blocked(1, at(MONDAY, 15, 29))
    assert not is_blocked(2, at(MONDAY, 15, 29))


def test_past_day_3_everything_is_blocked():
    assert is_blocked(4, at(THURSDAY, 9, 0))
    assert is_blocked(10, at(THURSDAY, 9, 0))


def test_the_cutoff_is_the_last_poll_before_the_close():
    assert BLOCK_CUTOFF == time(15, 0)
    assert CLOSE == time(15, 30)
    assert BLOCK_CUTOFF < CLOSE
