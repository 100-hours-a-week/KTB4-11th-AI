from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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
    log_level: str = "INFO"

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
