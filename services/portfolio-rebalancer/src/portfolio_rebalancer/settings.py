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
    backend_jwt_secret: SecretStr
    # Who the token says it is. The Backend reads `sub` with Long.parseLong, so this is
    # the numeric id of the user whose accounts are being rebalanced, not a service name.
    backend_jwt_subject: str = "portfolio-rebalancer"
    # Must equal the Backend's JWT_ISSUER: its decoder validates the issuer claim.
    backend_jwt_issuer: str
