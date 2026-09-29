from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KiwoomAccount(BaseModel):
    app_key: str
    secret_key: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_COLLECTOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    questdb_conf: str
    kiwoom_accounts: list[KiwoomAccount] = Field(min_length=1)
    kiwoom_mode: Literal["real", "demo"] = "real"
    request_interval: float = 1.3
    postgres_dsn: str
    index_name: str = "KOSPI200"
