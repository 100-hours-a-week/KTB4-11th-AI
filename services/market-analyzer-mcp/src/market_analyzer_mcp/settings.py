from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_ANALYZER_MCP_",
        extra="ignore",
    )

    log_level: str = "INFO"
    # libpq URI for QuestDB's Postgres wire port, e.g. postgresql://admin:quest@questdb:8812/qdb
    questdb_dsn: str
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
    candle_limit: int = Field(default=200, gt=0)
