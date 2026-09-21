"""Configuration for the news-preprocessor."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_PREPROCESSOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
