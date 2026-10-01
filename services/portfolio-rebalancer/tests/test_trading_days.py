"""The exchange calendar, against real Korean holidays.

The ladder counts forward in sessions from the day the cycle began: day one is the
widest band, day three the narrowest, and after day three there is no rung left.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest
from portfolio_rebalancer.order.reservations import PRICE_BANDS, band_for
from portfolio_rebalancer.trading_days import (
    LADDER_DAYS,
    MARKET_CUTOFF,
    is_open,
    ladder_day,
)

KST = timezone(timedelta(hours=9))

MONDAY = date(2026, 9, 28)
TUESDAY = date(2026, 9, 29)
WEDNESDAY = date(2026, 9, 30)
THURSDAY = date(2026, 10, 1)
FRIDAY = date(2026, 10, 2)
# 2026-10-05 week: Chuseok on the Monday, 한글날 on the Friday.
CHUSEOK_MONDAY = date(2026, 10, 5)


def at(day: date, hour=11, minute=0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (MONDAY, True),
        (FRIDAY, True),
        (date(2026, 10, 3), False),
        (CHUSEOK_MONDAY, False),
        (date(2026, 10, 9), False),
    ],
)
def test_the_calendar_knows_korean_holidays(day, expected):
    """Weekdays were an approximation; 한글날 and 개천절 are not weekends."""
    assert is_open(day) is expected


def test_there_is_one_rung_per_band():
    assert LADDER_DAYS == len(PRICE_BANDS)


def test_the_cycle_starts_on_day_one():
    assert ladder_day(MONDAY, at(MONDAY)) == 1


def test_each_session_advances_one_rung():
    assert [ladder_day(MONDAY, at(MONDAY + timedelta(days=n))) for n in range(3)] == [1, 2, 3]


def test_the_ladder_is_spent_after_the_third_session():
    """There is no fourth band, so the order stops being moved."""
    assert ladder_day(MONDAY, at(THURSDAY)) == 0
    assert ladder_day(MONDAY, at(MONDAY + timedelta(days=14))) == 0


def test_a_shut_day_advances_nothing():
    """A Saturday is not a session, so it reports the rung the Friday reached rather than
    a fourth one. Nothing acts on it either way -- `narrow` returns on a shut day -- but
    the count must not drift while the market is closed."""
    saturday, sunday = date(2026, 10, 3), date(2026, 10, 4)

    assert ladder_day(WEDNESDAY, at(FRIDAY)) == 3
    assert ladder_day(WEDNESDAY, at(saturday)) == 3
    assert ladder_day(WEDNESDAY, at(sunday)) == 3
    # The next session is the fourth, and there is no fourth rung.
    assert ladder_day(WEDNESDAY, at(date(2026, 10, 6))) == 0


def test_a_cycle_starting_on_a_wednesday_reaches_day_three_on_the_friday():
    """The example: sessions are counted, not calendar days."""
    assert ladder_day(WEDNESDAY, at(WEDNESDAY)) == 1
    assert ladder_day(WEDNESDAY, at(THURSDAY)) == 2
    assert ladder_day(WEDNESDAY, at(FRIDAY)) == 3


def test_a_holiday_is_skipped_rather_than_spent():
    """Chuseok falls on the Monday, so the cycle starts on the Tuesday and its third day
    is the Thursday rather than the Wednesday."""
    tuesday, wednesday, thursday = date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8)

    assert ladder_day(tuesday, at(tuesday)) == 1
    assert ladder_day(tuesday, at(wednesday)) == 2
    assert ladder_day(tuesday, at(thursday)) == 3
    # 한글날 is the Friday: shut, so it stays on the Thursday's rung rather than spending
    # one. The next session is the Monday, which is the fourth and has no rung.
    assert ladder_day(tuesday, at(date(2026, 10, 9))) == 3
    assert ladder_day(tuesday, at(date(2026, 10, 12))) == 0


def test_the_last_rung_runs_out_at_the_cutoff_not_at_midnight():
    """KRX closes at 15:30, so the last pass that can act is 15:00."""
    assert ladder_day(MONDAY, at(WEDNESDAY, 14, 0)) == 3
    assert ladder_day(MONDAY, at(WEDNESDAY, 14, 59)) == 3
    assert ladder_day(MONDAY, at(WEDNESDAY, 15, 0)) == 0
    assert ladder_day(MONDAY, at(WEDNESDAY, 15, 29)) == 0


def test_the_cutoff_does_not_shorten_an_earlier_rung():
    """Only the third day ends at the cutoff; the others end at midnight."""
    assert ladder_day(MONDAY, at(TUESDAY, 15, 29)) == 2


def test_the_cutoff_is_the_last_poll_before_the_close():
    """Polling runs on the hour, so 15:00 is the last pass before 15:30."""
    from portfolio_rebalancer.trading_days import CLOSE

    assert MARKET_CUTOFF == time(15, 0)
    assert CLOSE == time(15, 30)
    assert MARKET_CUTOFF < CLOSE


def test_a_pass_before_the_cycle_began_is_on_no_rung():
    assert ladder_day(WEDNESDAY, at(MONDAY)) == 0


def test_the_bands_narrow_one_rung_per_session():
    """Day one is ±5%, day two ±3%, day three ±1%, and after that nothing."""
    bands = [band_for(ladder_day(MONDAY, at(MONDAY + timedelta(days=n)))) for n in range(4)]

    assert bands == [PRICE_BANDS[0], PRICE_BANDS[1], PRICE_BANDS[2], None]
