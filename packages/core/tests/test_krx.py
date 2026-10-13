from datetime import UTC, date, datetime, time

import pytest
from ktb_core.krx import (
    COVERED_THROUGH,
    KRX_HOLIDAYS,
    KST,
    MARKET_CLOSE,
    MARKET_OPEN,
    is_trading_day,
)


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 12), True),
        (date(2026, 10, 16), True),
        (date(2026, 10, 9), False),
        (date(2026, 10, 10), False),
        (date(2026, 10, 11), False),
        (date(2027, 1, 1), False),
    ],
)
def test_is_trading_day(day, expected):
    assert is_trading_day(day) is expected


def test_every_holiday_is_inside_the_covered_range():
    assert all(day <= COVERED_THROUGH for day in KRX_HOLIDAYS)


def test_regular_session_is_0900_to_1530_seoul_time():
    assert (MARKET_OPEN, MARKET_CLOSE) == (time(9, 0), time(15, 30))
    opening = datetime(2026, 10, 13, 0, 0, tzinfo=UTC).astimezone(KST)
    assert opening.time() == MARKET_OPEN
    assert opening.isoformat() == "2026-10-13T09:00:00+09:00"
