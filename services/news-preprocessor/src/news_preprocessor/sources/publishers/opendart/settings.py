from datetime import date
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_PREPROCESSOR_DISCLOSURE_",
        extra="ignore",
        hide_input_in_errors=True,
        env_ignore_empty=True,
    )

    archive_root: Path = Path.home() / ".local/share/ktb/disclosure-rag/archive"
    request_interval: float = Field(default=1, ge=0)
    attempts: int = Field(default=3, ge=1, le=3)
    max_archive_bytes: int = Field(default=32_000_000, gt=0)
    max_uncompressed_bytes: int = Field(default=64_000_000, gt=0)
    start_date: date | None = None
    end_date: date | None = None
    collection_hour: int = Field(default=5, ge=0, le=23)

    @model_validator(mode="after")
    def historical_range(self) -> Self:
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date and end_date must be set together")
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError("start_date must not follow end_date")
        return self
