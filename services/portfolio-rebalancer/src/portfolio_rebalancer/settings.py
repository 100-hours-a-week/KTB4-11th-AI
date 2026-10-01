from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTFOLIO_REBALANCER_", extra="ignore")

    postgres_dsn: str
    questdb_conf: str
    backend_url: str
    backend_jwt_secret: SecretStr = Field(min_length=32)
    backend_jwt_issuer: str = Field(min_length=1)
    band: float = Field(default=0.05, gt=0, lt=1)
    buy_buffer: float = Field(default=0.02, ge=0, lt=1)
    log_level: str = "INFO"
