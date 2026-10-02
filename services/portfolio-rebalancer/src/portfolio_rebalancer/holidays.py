from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
FIRST_RUN, LAST_RUN = 9, 15
RUNS_PER_DAY = LAST_RUN - FIRST_RUN + 1

# Weekdays that KIS chk-holiday reports with opnd_yn "N". Verify against KIS before
# extending.
KRX_HOLIDAYS = frozenset(
    {
        date(2026, 10, 5),
        date(2026, 10, 9),
        date(2026, 12, 25),
        date(2026, 12, 31),
        date(2027, 1, 1),
        date(2027, 2, 8),
        date(2027, 2, 9),
        date(2027, 3, 1),
        date(2027, 5, 5),
        date(2027, 5, 13),
        date(2027, 8, 16),
        date(2027, 9, 14),
        date(2027, 9, 15),
        date(2027, 9, 16),
    }
)
COVERED_THROUGH = date(2027, 10, 1)


def _open(day: date) -> bool:
    return day.weekday() < 5 and day not in KRX_HOLIDAYS


def in_session(now: datetime) -> bool:
    local = now.astimezone(KST)
    return _open(local.date()) and FIRST_RUN <= local.hour <= LAST_RUN


def hours_left(now: datetime) -> tuple[int, int]:
    local = now.astimezone(KST)
    today = local.date()
    if today > COVERED_THROUGH:
        raise RuntimeError(f"KRX_HOLIDAYS ends at {COVERED_THROUGH}; extend it")
    monday = today - timedelta(days=today.weekday())
    week = [day for day in (monday + timedelta(days=i) for i in range(5)) if _open(day)]
    left = sum(day > today for day in week) * RUNS_PER_DAY
    if today in week:
        left += LAST_RUN + 1 - min(max(local.hour, FIRST_RUN), LAST_RUN + 1)
    return left, len(week) * RUNS_PER_DAY
