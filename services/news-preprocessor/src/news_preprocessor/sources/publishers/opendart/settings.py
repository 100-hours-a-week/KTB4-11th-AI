from pathlib import Path

from pydantic import Field
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
    attempts: int = Field(default=3, ge=1, le=10)
    max_archive_bytes: int = Field(default=32_000_000, gt=0)
    max_uncompressed_bytes: int = Field(default=64_000_000, gt=0)
