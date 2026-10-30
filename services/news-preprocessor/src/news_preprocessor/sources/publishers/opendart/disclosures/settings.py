from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_PREPROCESSOR_DISCLOSURE_",
        extra="ignore",
        hide_input_in_errors=True,
        env_ignore_empty=True,
        populate_by_name=True,
    )

    dart_api_key: SecretStr = Field(min_length=1, validation_alias="NEWS_PREPROCESSOR_DART_API_KEY")
    archive_root: Path = Path.home() / ".local/share/ktb/disclosure-rag/archive"
    request_interval: float = Field(default=1, ge=0)
    attempts: int = Field(default=3, ge=1, le=10)
    max_archive_bytes: int = Field(default=32_000_000, gt=0)
    max_uncompressed_bytes: int = Field(default=64_000_000, gt=0)
    max_attempts: int = Field(default=5, ge=1, le=5)
    refresh_days: int = Field(default=30, ge=0)
    collect_financials: bool = True
    document_limit: int = Field(default=100, ge=1)
