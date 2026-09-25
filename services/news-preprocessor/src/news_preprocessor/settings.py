from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_PREPROCESSOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    user_agent: str = "ktb-ai/0.1"
    postgres_dsn: str
    embed_batch_limit: int = Field(default=100, gt=0)
