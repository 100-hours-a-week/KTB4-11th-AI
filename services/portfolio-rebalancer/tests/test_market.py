from datetime import UTC, datetime, timedelta

from portfolio_rebalancer.market import recent_closes

MIDNIGHT_KST = datetime(2026, 10, 13, 15, tzinfo=UTC)


def day(n):
    return (MIDNIGHT_KST - timedelta(days=n)).replace(tzinfo=None)


def test_the_last_n_closes_before_the_cutoff_are_kept_in_order():
    records = [{"symbol": "005930", "ts": day(n), "close": 100 + n} for n in range(4, 0, -1)]

    assert recent_closes(records, MIDNIGHT_KST, 3) == {"005930": [103.0, 102.0, 101.0]}


def test_todays_bar_is_excluded():
    records = [
        {"symbol": "005930", "ts": day(2), "close": 1},
        {"symbol": "005930", "ts": day(1), "close": 2},
        {"symbol": "005930", "ts": day(0), "close": 999},
    ]

    assert recent_closes(records, MIDNIGHT_KST, 2) == {"005930": [1.0, 2.0]}


def test_a_symbol_with_too_few_closes_is_dropped():
    records = [{"symbol": "000660", "ts": day(1), "close": 1}]

    assert recent_closes(records, MIDNIGHT_KST, 2) == {}
