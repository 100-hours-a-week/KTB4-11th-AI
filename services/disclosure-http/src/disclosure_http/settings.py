from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DISCLOSURE_HTTP_", extra="ignore", hide_input_in_errors=True
    )

    postgres_dsn: str
    evaluation_clock_file: str
    public_url: str = Field(pattern=r"^https?://")
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
    log_level: str = "INFO"
