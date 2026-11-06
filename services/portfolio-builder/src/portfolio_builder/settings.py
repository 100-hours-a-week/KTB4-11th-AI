from typing import Literal

from pydantic import Field, SecretStr
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
    competition_queue_url: str = ""
    competition_visibility_timeout: int = Field(default=300, gt=0)
    competition_stale_running_seconds: int = Field(default=3600, gt=0)
    competition_max_attempts: int = Field(default=3, gt=0)
    backend_base_uri: str = ""
    backend_jwt_secret: SecretStr = SecretStr("")
    backend_jwt_issuer: str = ""
    backend_timeout: int = Field(default=10, gt=0)
    aws_region: str = "ap-northeast-2"
    jev_base_uri: str = "https://openrouter.ai/api/v1"
    jev_model: str = "typesafe/jev-1.14"
    jev_timeout: int = Field(default=20, gt=0)
    jev_noul_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    jev_choice_min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
