from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MARKET_SYNCER_", hide_input_in_errors=True)

    postgres_dsn: str
    kiwoom_app_key: SecretStr
    kiwoom_secret_key: SecretStr
    kiwoom_mode: Literal["real", "demo"] = "real"
    kiwoom_request_interval: float = Field(default=0.2, ge=0)
    dart_api_key: SecretStr
    log_level: str = "INFO"
