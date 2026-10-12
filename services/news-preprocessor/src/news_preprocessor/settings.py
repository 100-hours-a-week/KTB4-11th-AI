from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_PREPROCESSOR_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    log_level: str = "INFO"
    user_agent: str = "ktb-ai/0.1"
    postgres_dsn: str
    embed_batch_limit: int = Field(default=100, gt=0)
    dart_api_key: SecretStr
    # Named like the other KTB_EMBEDDING_* variables, which describe the same endpoint.
    embedding_api_key: SecretStr | None = Field(
        default=None, validation_alias="KTB_EMBEDDING_API_KEY"
    )
