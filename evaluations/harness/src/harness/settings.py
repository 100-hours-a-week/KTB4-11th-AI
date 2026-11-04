from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_", extra="ignore", hide_input_in_errors=True
    )

    spec_file: str
    results_dir: str = "results"
    run_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]+$")
    prices_url: str
    clock_file: str
    information_variant: Literal["none", "market", "raw", "enriched", "disclosures"] = "none"
    information_version: str = "1"
    market_url: str | None = None
    news_url: str | None = None
    features_url: str | None = None
    disclosures_url: str | None = None
    decision_system: Literal["equal-weight", "buy-and-hold", "jev-stub", "docker-agent"] = (
        "equal-weight"
    )
    agent_worker_url: str | None = None
    submission_host: str = "0.0.0.0"
    submission_port: int = Field(default=8100, gt=0, lt=65536)
    submission_url: str | None = Field(default=None, pattern=r"^https?://")
    decision_timeout_seconds: float = Field(default=900, gt=0)
    http_timeout_seconds: float = Field(default=30, gt=0)
    code_commit: str | None = None
    image_digest: str | None = None
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _agent_needs_endpoints(self) -> Self:
        if self.decision_system == "docker-agent" and not (
            self.agent_worker_url and self.submission_url
        ):
            raise ValueError("docker-agent needs agent_worker_url and submission_url")
        return self


class AgentWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_AGENT_", extra="ignore", hide_input_in_errors=True
    )

    binary: str = "docker-agent"
    run_args: str = "run --exec --yolo"
    model_provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_base_url: str | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    instruction_file: str | None = None
    api_key_file: str | None = None
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    output_chars: int = Field(default=20_000, gt=0)
    host: str = "0.0.0.0"
    port: int = Field(default=8200, gt=0, lt=65536)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _key_file_needs_env(self) -> Self:
        if (self.api_key_file is None) != (self.api_key_env is None):
            raise ValueError("api_key_file and api_key_env are set together")
        return self


class FeaturesSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_FEATURES_", extra="ignore", hide_input_in_errors=True
    )

    market_url: str
    public_url: str = Field(pattern=r"^https?://")
    timeout_seconds: float = Field(default=30, gt=0)
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
    log_level: str = "INFO"
