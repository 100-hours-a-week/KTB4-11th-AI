from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DISCLOSURE_RETRIEVAL_",
        extra="ignore",
        hide_input_in_errors=True,
        env_ignore_empty=True,
    )

    postgres_dsn: str
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
    log_level: str = "INFO"
    embedding_base_uri: str = Field(min_length=1, validation_alias="DISCLOSURE_EMBEDDING_BASE_URI")
    embedding_model: str = Field(min_length=1, validation_alias="DISCLOSURE_EMBEDDING_MODEL")
    embedding_api_key: SecretStr | None = Field(
        default=None, validation_alias="DISCLOSURE_EMBEDDING_API_KEY"
    )
    query_instruction: str = Field(
        default=(
            "Instruct: Retrieve disclosure passages that answer the financial question.\nQuery: "
        ),
        validation_alias="DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION",
    )
    dimensions: int = Field(
        default=2000, ge=2000, le=2000, validation_alias="DISCLOSURE_EMBEDDING_DIMENSIONS"
    )
    max_tokens: int = Field(
        default=4096, ge=1, le=16384, validation_alias="DISCLOSURE_EMBEDDING_MAX_TOKENS"
    )
    timeout: float = Field(default=30, gt=0, le=120)
    # No default cutoff: an empty result must mean "nothing relevant", not "filtered out".
    max_distance: float | None = Field(default=None, ge=0, le=2)
