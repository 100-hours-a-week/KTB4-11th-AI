from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_ANALYZER_MCP_",
        extra="ignore",
    )

    log_level: str = "INFO"
    questdb_dsn: str

    host: str = "0.0.0.0"
    port: int = 8000

    # Indicators need warm-up history: RSI 15 candles, MACD 34. Read generously.
    window: int = Field(default=300, gt=0)
