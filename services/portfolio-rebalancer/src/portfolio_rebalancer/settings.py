from typing import Annotated

from pydantic import SecretStr, StringConstraints
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
    # The Backend parses `sub` as a Long user id.
    backend_jwt_subject: Annotated[str, StringConstraints(pattern=r"^[0-9]+$")]
    # Must equal the Backend's JWT_ISSUER: its decoder validates the issuer claim.
    backend_jwt_issuer: str
