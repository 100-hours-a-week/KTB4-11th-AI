from datetime import time
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_STREAMER_", extra="ignore", hide_input_in_errors=True
    )

    postgres_dsn: str
    kiwoom_app_key: SecretStr = Field(min_length=1)
    kiwoom_secret_key: SecretStr = Field(min_length=1)
    kiwoom_mode: Literal["real", "demo"] = "real"
    index_name: str = "KOSPI200"
    stop_at: time = time(15, 40)
    test_mode: bool = False
    valkey_host: str = Field(min_length=1)
    valkey_port: int = 6379
    valkey_username: str | None = None
    valkey_cache_name: str | None = None
    valkey_tls: bool = False
    valkey_auth_mode: Literal["none", "iam"] = "none"
    aws_region: str = "ap-northeast-2"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _iam_needs_identity(self) -> Self:
        if self.valkey_auth_mode == "iam" and not (self.valkey_username and self.valkey_cache_name):
            raise ValueError("iam auth needs valkey_username and valkey_cache_name")
        return self
