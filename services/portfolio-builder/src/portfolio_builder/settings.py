"""Configuration for the portfolio builder.

``questdb_dsn`` is a read connection over QuestDB's Postgres wire protocol
(port 8812). This service never creates, alters or drops a QuestDB table:
QuestDB's schema and ingestion are owned outside this repository.
"""

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
