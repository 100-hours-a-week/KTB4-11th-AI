from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_HTTP_", extra="ignore", hide_input_in_errors=True
    )

    questdb_conf: str
    valkey_host: str
    valkey_port: int = 6379
    valkey_username: str | None = None
    valkey_cache_name: str | None = None
    valkey_tls: bool = False
    valkey_auth_mode: Literal["none", "iam"] = "none"
    aws_region: str = "ap-northeast-2"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _iam_needs_identity(self) -> Self:
        if self.valkey_auth_mode == "iam" and not (self.valkey_username and self.valkey_cache_name):
            raise ValueError("iam auth needs valkey_username and valkey_cache_name")
        return self
