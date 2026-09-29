from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_REBALANCER_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
    backend_url: str
    # A credential: SecretStr keeps it out of logs and repr.
    backend_jwt_secret: SecretStr
    # Who the token says it is. Deployment config rather than code, because the Backend
    # decides which identity may read every user.
    backend_jwt_subject: str = "portfolio-rebalancer"
