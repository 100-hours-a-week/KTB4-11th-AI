from datetime import date

import pytest
from ktb_core.krx import COVERED_THROUGH, KRX_HOLIDAYS, is_trading_day


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
