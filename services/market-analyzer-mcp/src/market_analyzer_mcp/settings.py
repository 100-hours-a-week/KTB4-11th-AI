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
    port: int = Field(default=8000, gt=0, lt=65536)

    # RSI needs 15 candles and MACD 34 before either produces a value, so read well
    # past the warm-up rather than only as far back as the newest reading needs.
    candle_limit: int = Field(default=200, gt=0)
