"""The exchange calendar, against real Korean holidays."""

from datetime import date, datetime, timedelta, timezone

import pytest
from portfolio_rebalancer.decide.reservations import PRICE_BANDS, band_for
from portfolio_rebalancer.decide.trading_days import (
    MARKET_CUTOFF,
    days_left,
    is_open,
    week_deadline,
)

KST = timezone(timedelta(hours=9))

# 2026-09-28 is a full week. 2026-10-05 has only three sessions: Chuseok on the Monday
# and 한글날 on the Friday.
FULL_WEEK = date(2026, 9, 28)
SHORT_WEEK = date(2026, 10, 5)


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


def test_a_full_week_gives_five_days():
    assert days_left(FULL_WEEK, at(FULL_WEEK)) == 5


def test_a_holiday_week_gives_what_is_left_of_it():
    """A Chuseok week is three sessions, so the whole cycle has three."""
    assert days_left(SHORT_WEEK, at(SHORT_WEEK)) == 3


def test_each_session_spends_one_day():
    spent = [days_left(FULL_WEEK, at(FULL_WEEK + timedelta(days=n))) for n in range(5)]

    assert spent == [5, 4, 3, 2, 1]


def test_a_weekend_consumes_nothing():
    friday, saturday = date(2026, 10, 2), date(2026, 10, 3)

    assert days_left(FULL_WEEK, at(friday)) == 1
    assert days_left(FULL_WEEK, at(saturday)) == 0


def test_a_holiday_consumes_nothing():
    """한글날 is a Friday. Thursday is the last session, so Friday has none left."""
    assert days_left(SHORT_WEEK, at(date(2026, 10, 8))) == 1
    assert days_left(SHORT_WEEK, at(date(2026, 10, 9))) == 0


def test_the_deadline_is_the_last_session_of_the_week():
    """The next judgement lands the week after, so an order still working then would act
    on a portfolio that has been replaced."""
    assert week_deadline(FULL_WEEK) == date(2026, 10, 2)
    assert week_deadline(SHORT_WEEK) == date(2026, 10, 8)


def test_the_last_day_runs_out_at_the_cutoff_not_at_midnight():
    """KRX closes at 15:30, so the market order has to be in before that rather than the
    morning after."""
    friday = date(2026, 10, 2)

    assert days_left(FULL_WEEK, at(friday, 14, 0)) == 1
    assert days_left(FULL_WEEK, at(friday, MARKET_CUTOFF.hour, MARKET_CUTOFF.minute)) == 0
    assert days_left(FULL_WEEK, at(friday, 15, 29)) == 0


def test_the_cutoff_leaves_an_hour_before_the_close():
    """Polling at least hourly then always puts a pass inside the window."""
    from portfolio_rebalancer.decide.trading_days import CLOSE

    before_close = datetime.combine(date(2026, 1, 1), CLOSE) - datetime.combine(
        date(2026, 1, 1), MARKET_CUTOFF
    )

    assert before_close >= timedelta(hours=1)


def test_the_cutoff_does_not_shorten_an_earlier_day():
    """Only the last day ends at the cutoff; the others end at midnight."""
    thursday = date(2026, 10, 1)

    assert days_left(FULL_WEEK, at(thursday, 15, 29)) == 2


def test_two_days_of_selling_leaves_three_for_buying():
    """Five sessions shared: the example the deadline exists for."""
    wednesday = date(2026, 9, 30)

    left = days_left(FULL_WEEK, at(wednesday))

    assert left == 3
    assert band_for(left) == PRICE_BANDS[0]


def test_past_the_deadline_there_is_nothing_left():
    assert days_left(FULL_WEEK, at(FULL_WEEK + timedelta(days=14))) == 0
