"""Configuration for the news-preprocessor."""

from ktb_core.embedding import EMBEDDING_BASE_URI_ENV
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_PREPROCESSOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
    embedding_base_uri: str = Field(validation_alias=EMBEDDING_BASE_URI_ENV)
    embed_batch_limit: int = Field(default=100, gt=0)
