"""Configuration for the portfolio-builder."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_BUILDER_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
    questdb_dsn: str
    news_clusterer_url: str
