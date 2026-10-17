from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_COLLECTOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    questdb_conf: str
    kiwoom_app_key: SecretStr = Field(min_length=1)
    kiwoom_secret_key: SecretStr = Field(min_length=1)
    kiwoom_mode: Literal["real", "demo"] = "real"
    request_interval: float = 1.3
    postgres_dsn: str
    index_name: str = "KOSPI200"
    valkey_host: str = Field(min_length=1)
    valkey_port: int = 6379
    valkey_username: str | None = None
    valkey_cache_name: str | None = None
    valkey_tls: bool = False
    valkey_auth_mode: Literal["none", "iam"] = "none"
    aws_region: str = "ap-northeast-2"

    @model_validator(mode="after")
    def _iam_needs_identity(self) -> Self:
        if self.valkey_auth_mode == "iam" and not (self.valkey_username and self.valkey_cache_name):
            raise ValueError("iam auth needs valkey_username and valkey_cache_name")
        return self
