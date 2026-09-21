"""Configuration for the news clusterer."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_CLUSTERER_",
        extra="ignore",
    )

    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    postgres_dsn: str
