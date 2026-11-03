from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from ktb_core.krx import KST


@dataclass(frozen=True)
class Schedule:
    every: Literal["day", "week"]
    start: date
    end: date
    at: time
    weekday: int = 0

    def times(self) -> tuple[datetime, ...]:
        days = (self.start + timedelta(days=n) for n in range((self.end - self.start).days + 1))
        return tuple(
            datetime.combine(day, self.at, tzinfo=KST)
            for day in days
            if day.weekday() < 5 and (self.every == "day" or day.weekday() == self.weekday)
        )


def weekly_single_shots(
    first: date, last: date, weekday: int, at: time, end_offset_days: int, end_at: time
) -> list[tuple[str, datetime, datetime]]:
    episodes = []
    for decided in Schedule("week", first, last, at, weekday).times():
        end = datetime.combine(decided.date() + timedelta(days=end_offset_days), end_at, KST)
        episodes.append((f"week-{decided.date().isoformat()}", decided, end))
    return episodes
