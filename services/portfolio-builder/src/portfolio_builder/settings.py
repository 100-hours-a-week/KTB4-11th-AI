from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
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
    log_level: str = "INFO"
    explain_result_chars: int = Field(default=2000, gt=0)
    explain_max_tokens: int = Field(default=16000, gt=0)
    submitter: Literal["database", "evaluation"] = "database"
    evaluation_output_dir: str | None = None

    @model_validator(mode="after")
    def _evaluation_needs_output(self) -> Self:
        if (self.submitter == "evaluation") != (self.evaluation_output_dir is not None):
            raise ValueError("evaluation_output_dir is set exactly when submitter is evaluation")
        return self
