from datetime import date, datetime, time
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from harness.evaluator import MetricRules
from harness.loop import EpisodeSpec
from harness.schedule import Schedule, weekly_single_shots

NAME = r"^[A-Za-z0-9._-]+$"


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScheduleIn(Frozen):
    every: Literal["day", "week"]
    start: date
    end: date
    at: time
    weekday: int = Field(default=0, ge=0, le=4)


class EpisodeIn(Frozen):
    id: str = Field(pattern=NAME)
    decision_times: list[AwareDatetime] | None = None
    schedule: ScheduleIn | None = None
    end: AwareDatetime

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.decision_times is None) == (self.schedule is None):
            raise ValueError("give exactly one of decision_times or schedule")
        return self

    def times(self) -> tuple[datetime, ...]:
        if self.schedule is None:
            return tuple(self.decision_times or ())
        s = self.schedule
        return Schedule(s.every, s.start, s.end, s.at, s.weekday).times()


class WeeklySingleShotIn(Frozen):
    first_week: date
    last_week: date
    weekday: int = Field(default=0, ge=0, le=4)
    at: time = time(8, 0)
    end_offset_days: int = Field(default=4, ge=0, le=6)
    end_at: time = time(23, 59)


class MetricRulesIn(Frozen):
    periods_per_year: int = Field(default=252, gt=0)
    target_return: float = 0.0
    risk_free_rate: float = 0.0


class ExperimentSpec(Frozen):
    experiment_id: str = Field(pattern=NAME)
    dataset_version: str | None = None
    universe: list[str] = Field(min_length=1)
    initial_capital: float = Field(gt=0)
    fee_rate: float = Field(ge=0, lt=1)
    sell_tax_rate: float = Field(ge=0, lt=1)
    metrics: MetricRulesIn = MetricRulesIn()
    episodes: list[EpisodeIn] = []
    weekly_single_shot: WeeklySingleShotIn | None = None

    @model_validator(mode="after")
    def _valid_universe_and_episodes(self) -> Self:
        if len(set(self.universe)) != len(self.universe):
            raise ValueError("universe has duplicate codes")
        if any(len(code) != 6 or not code.isalnum() for code in self.universe):
            raise ValueError("universe codes are 6 alphanumeric characters")
        ids = [episode_id for episode_id, _ in self.episode_specs()]
        if not ids:
            raise ValueError("give episodes or weekly_single_shot")
        if len(set(ids)) != len(ids):
            raise ValueError("episode ids must be unique")
        return self

    def rules(self) -> MetricRules:
        return MetricRules(**self.metrics.model_dump())

    def episode_specs(self) -> list[tuple[str, EpisodeSpec]]:
        def spec(times, end) -> EpisodeSpec:
            return EpisodeSpec(
                tuple(self.universe),
                self.initial_capital,
                tuple(times),
                end,
                self.fee_rate,
                self.sell_tax_rate,
            )

        specs = [(episode.id, spec(episode.times(), episode.end)) for episode in self.episodes]
        if self.weekly_single_shot is not None:
            w = self.weekly_single_shot
            specs.extend(
                (episode_id, spec((decided,), end))
                for episode_id, decided, end in weekly_single_shots(
                    w.first_week, w.last_week, w.weekday, w.at, w.end_offset_days, w.end_at
                )
            )
        return specs
