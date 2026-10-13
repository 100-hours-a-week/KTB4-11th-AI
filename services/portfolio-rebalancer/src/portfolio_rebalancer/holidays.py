from datetime import datetime, timedelta

from ktb_core.krx import COVERED_THROUGH, KST, MARKET_CLOSE, MARKET_OPEN, is_trading_day

FIRST_RUN, LAST_RUN = MARKET_OPEN.hour, MARKET_CLOSE.hour
RUNS_PER_DAY = LAST_RUN - FIRST_RUN + 1


def in_session(now: datetime) -> bool:
    local = now.astimezone(KST)
    return is_trading_day(local.date()) and FIRST_RUN <= local.hour <= LAST_RUN


def hours_left(now: datetime) -> tuple[int, int]:
    local = now.astimezone(KST)
    today = local.date()
    if today > COVERED_THROUGH:
        raise RuntimeError(f"KRX_HOLIDAYS ends at {COVERED_THROUGH}; extend it")
    monday = today - timedelta(days=today.weekday())
    week = [day for day in (monday + timedelta(days=i) for i in range(5)) if is_trading_day(day)]
    left = sum(day > today for day in week) * RUNS_PER_DAY
    if today in week:
        left += LAST_RUN + 1 - min(max(local.hour, FIRST_RUN), LAST_RUN + 1)
    return left, len(week) * RUNS_PER_DAY
