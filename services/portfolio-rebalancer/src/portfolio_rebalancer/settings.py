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
    # There is no subject to configure: the snapshot route wants the literal `ai-server`
    # and every order is signed for the user who owns the account it is placed on.
    # Must equal the Backend's JWT_ISSUER: its decoder validates the issuer claim.
    backend_jwt_issuer: str
