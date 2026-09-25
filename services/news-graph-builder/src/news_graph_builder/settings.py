from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_GRAPH_BUILDER_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    log_level: str = "INFO"
    postgres_dsn: str
    llm_base_uri: str
    llm_model: str
    kiwoom_app_key: SecretStr
    kiwoom_secret_key: SecretStr
    dart_api_key: SecretStr
    kiwoom_base_uri: str = "https://api.kiwoom.com"
    summary_max_chars: int = Field(default=24000, gt=0)
    llm_timeout: float = Field(default=120, gt=0)
    max_entities: int = Field(default=30, gt=0)
    max_relations: int = Field(default=50, gt=0)
