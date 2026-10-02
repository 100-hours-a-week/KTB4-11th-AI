from datetime import date, datetime

import pytest
from portfolio_rebalancer import holidays
from portfolio_rebalancer.holidays import KST, hours_left, in_session


def at(*args):
    return datetime(*args, tzinfo=KST)


@pytest.fixture
def two_day_week(monkeypatch):
    monkeypatch.setattr(
        holidays,
        "KRX_HOLIDAYS",
        frozenset({date(2026, 10, 19), date(2026, 10, 21), date(2026, 10, 23)}),
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(2026, 10, 19, 9), (14, 14)),
        (at(2026, 10, 20, 15), (8, 14)),
        (at(2026, 10, 21, 10), (7, 14)),
        (at(2026, 10, 22, 15), (1, 14)),
    ],
)
def test_a_two_day_week_counts_only_tuesday_and_thursday(two_day_week, now, expected):
    assert hours_left(now) == expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(2026, 10, 19, 10), False),
        (at(2026, 10, 20, 10), True),
        (at(2026, 10, 21, 10), False),
        (at(2026, 10, 22, 10), True),
        (at(2026, 10, 23, 10), False),
    ],
)
def test_a_two_day_week_trades_only_on_tuesday_and_thursday(two_day_week, now, expected):
    assert in_session(now) is expected


def test_monday_open_of_a_full_week_has_every_run_left():
    assert hours_left(at(2026, 10, 12, 9)) == (35, 35)


def test_friday_close_is_the_last_run():
    assert hours_left(at(2026, 10, 16, 15)) == (1, 35)


def test_midweek_counts_the_rest_of_today_and_the_later_days():
    assert hours_left(at(2026, 10, 14, 12)) == (18, 35)


def test_a_holiday_week_shrinks_the_week():
    assert hours_left(at(2026, 10, 6, 9)) == (21, 21)


def test_a_holiday_friday_makes_thursday_close_the_last_run():
    assert hours_left(at(2026, 10, 8, 15)) == (1, 21)


def test_a_utc_clock_is_read_in_kst():
    assert hours_left(datetime.fromisoformat("2026-10-16T06:00:00+00:00")) == (1, 35)


def test_past_the_calendar_raises():
    with pytest.raises(RuntimeError, match="KRX_HOLIDAYS"):
        hours_left(at(2028, 1, 3, 9))


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(2026, 10, 12, 9), True),
        (at(2026, 10, 12, 15), True),
        (at(2026, 10, 12, 8), False),
        (at(2026, 10, 12, 16), False),
        (at(2026, 10, 9, 10), False),
        (at(2026, 10, 10, 10), False),
    ],
)
def test_in_session(now, expected):
    assert in_session(now) is expected
