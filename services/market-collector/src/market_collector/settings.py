"""Configuration for the market-collector."""

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
    questdb_dsn: str
    questdb_ilp_host: str
    questdb_ilp_port: int = 9009
    kiwoom_accounts: list[KiwoomAccount] = Field(min_length=1)
    request_interval: float = 1.3
    theme_date_tps: list[int] = [5, 20, 60]
    cursor_path: str = "var/market-collector/cursors.json"
