from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DISCLOSURE_INDEXER_",
        extra="ignore",
        hide_input_in_errors=True,
        env_ignore_empty=True,
    )
    dart_api_key: SecretStr = Field(min_length=1)
    archive_root: Path = Path.home() / ".local/share/ktb/disclosure-rag/archive"
    business_year: int | None = Field(default=None, ge=2015, le=2100)
    request_interval: float = Field(default=1, ge=0)
    attempts: int = Field(default=3, ge=1, le=10)
    max_archive_bytes: int = Field(default=32_000_000, gt=0)
    max_uncompressed_bytes: int = Field(default=64_000_000, gt=0)
    max_attempts: int = Field(default=5, ge=1, le=5)
    postgres_dsn: str
    embedding_base_uri: str = Field(min_length=1, validation_alias="DISCLOSURE_EMBEDDING_BASE_URI")
    embedding_model: str = Field(min_length=1, validation_alias="DISCLOSURE_EMBEDDING_MODEL")
    embedding_api_key: SecretStr | None = Field(
        default=None, validation_alias="DISCLOSURE_EMBEDDING_API_KEY"
    )
    dimensions: int = Field(
        default=2000, ge=2000, le=2000, validation_alias="DISCLOSURE_EMBEDDING_DIMENSIONS"
    )
    max_tokens: int = Field(
        default=4096, ge=1, le=16384, validation_alias="DISCLOSURE_EMBEDDING_MAX_TOKENS"
    )
    # Part of the index signature: changing the query prefix changes the vector space, so
    # stored indexes must stop matching it.
    query_instruction: str = Field(
        default=(
            "Instruct: Retrieve disclosure passages that answer the financial question.\nQuery: "
        ),
        validation_alias="DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION",
    )
    chunk_bytes: int = Field(default=2400, ge=64, le=10000)
    overlap_bytes: int = Field(default=300, ge=0)
    input_bytes: int = Field(default=12000, ge=256, le=16384)
    batch_size: int = Field(default=16, ge=1, le=100)
    timeout: float = Field(default=120, gt=0)
    lease_seconds: int = Field(default=600, ge=30)
    document_limit: int = Field(default=100, ge=1)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def valid_budgets(self):
        if self.overlap_bytes >= self.chunk_bytes or self.input_bytes < self.chunk_bytes + 100:
            raise ValueError("invalid chunk or input budget")
        if self.lease_seconds <= self.timeout + 10:
            raise ValueError("lease must exceed embedding timeout")
        return self
