from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_REBALANCER_HTTP_",
        extra="ignore",
    )

    log_level: str = "INFO"
    questdb_conf: str
    backend_url: str
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
