from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_GRAPH_BUILDER_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    log_level: str = "INFO"
    postgres_dsn: str
