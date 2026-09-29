from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_REBALANCER_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
    questdb_conf: str
    backend_url: str
    # A credential: SecretStr keeps it out of logs and repr.
    backend_jwt: SecretStr
