from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_BUILDER_", extra="ignore", hide_input_in_errors=True
    )

    postgres_dsn: str
    market_http_base_uri: str
    news_http_base_uri: str
    llm_api_key: SecretStr = Field(min_length=1)
    llm_model: str
    thinking_level: Literal["none", "minimal", "low", "medium", "high", "xhigh"] = "medium"
    news_window_days: int = Field(default=7, gt=0)
    max_turns: int = Field(default=150, gt=0)
    disclosure_enabled: bool = Field(default=False, validation_alias="DISCLOSURE_ENABLED")
    log_level: str = "INFO"
    explain_result_chars: int = Field(default=2000, gt=0)
    explain_max_tokens: int = Field(default=16000, gt=0)
    competition_stale_running_seconds: int = Field(default=3600, gt=0)
    competition_max_attempts: int = Field(default=3, gt=0)
    competition_enabled: bool = False
    backend_base_uri: str = ""
    backend_jwt_secret: SecretStr | None = Field(default=None, min_length=32)
    backend_jwt_issuer: str = ""

    @field_validator("backend_jwt_secret", mode="before")
    @classmethod
    def empty_backend_secret(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def participant_lookup_configuration(self) -> Self:
        if self.competition_enabled:
            missing = [
                name
                for name in ("backend_base_uri", "backend_jwt_secret", "backend_jwt_issuer")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    "competition mode requires backend settings: " + ", ".join(missing)
                )
        return self


class CompetitionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_BUILDER_COMPETITION_", extra="ignore", hide_input_in_errors=True
    )

    queue_url: str = Field(min_length=1)
    visibility_timeout: int = Field(default=300, gt=0, le=43200)
    aws_region: str = "ap-northeast-2"
    jev_api_key: SecretStr | None = Field(default=None, min_length=1)
    jev_base_uri: str = "https://openrouter.ai/api/v1"
    jev_model: str = "typesafe/jev-1.14"
    jev_timeout: int = Field(default=20, gt=0)
    jev_noul_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    jev_choice_min_confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("queue_url")
    @classmethod
    def validate_queue_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not parsed.path.strip("/")
        ):
            raise ValueError("queue_url must be a queue URL")
        return value
