"""Configuration for the news preprocessor.

Settings live in the service, not in ktb-core: a cron job, an HTTP server and
a queue consumer share almost no configuration, and a shared base class would
make all three redeploy whenever one of them needs a new field.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_PREPROCESSOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
