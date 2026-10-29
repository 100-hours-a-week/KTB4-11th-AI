from pathlib import Path
from typing import Literal

from ktb_core.disclosure_models import embedding_space
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
    # How long a completed collection window stays open to late corrections.
    recheck_days: int = Field(default=400, ge=1)
    # How far back to re-fetch a receipt and compare its hash, catching a replaced source.
    refresh_days: int = Field(default=30, ge=0)
    collect_financials: bool = True
    postgres_dsn: str
    embedding_base_uri: str = Field(default="", validation_alias="DISCLOSURE_EMBEDDING_BASE_URI")
    embedding_model: str = Field(default="", validation_alias="DISCLOSURE_EMBEDDING_MODEL")
    embedding_api_key: SecretStr | None = Field(
        default=None, validation_alias="DISCLOSURE_EMBEDDING_API_KEY"
    )
    dimensions: int = Field(
        default=2000, ge=1024, le=2000, validation_alias="DISCLOSURE_EMBEDDING_DIMENSIONS"
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
    context_bytes: int = Field(default=12000, ge=256, le=15000)
    batch_size: int = Field(default=16, ge=1, le=100)
    timeout: float = Field(default=120, gt=0)
    lease_seconds: int = Field(default=600, ge=30)
    document_limit: int = Field(default=100, ge=1)
    log_level: str = "INFO"

    inference_backend: Literal["openai", "modal"] = Field(
        default="openai", validation_alias="DISCLOSURE_INFERENCE_BACKEND"
    )
    modal_key: SecretStr | None = Field(default=None, validation_alias="DISCLOSURE_MODAL_KEY")
    modal_secret: SecretStr | None = Field(default=None, validation_alias="DISCLOSURE_MODAL_SECRET")
    modal_document_url: str = Field(default="", validation_alias="DISCLOSURE_MODAL_DOCUMENT_URL")

    @property
    def index_model(self) -> str:
        return embedding_space() if self.inference_backend == "modal" else self.embedding_model

    @model_validator(mode="after")
    def valid_inference(self):
        if self.inference_backend == "modal":
            if self.dimensions != 1024 or self.max_tokens != 16384:
                raise ValueError("Modal requires 1024 dimensions and 16384 tokens")
            if not self.modal_key or not self.modal_secret:
                raise ValueError("Modal proxy credentials are required")
            if not self.modal_document_url.startswith("https://"):
                raise ValueError("Modal endpoint must use HTTPS")
        elif self.dimensions != 2000 or not self.embedding_model or not self.embedding_base_uri:
            raise ValueError("Legacy embeddings require a model, base URI and 2000 dimensions")
        return self

    @model_validator(mode="after")
    def valid_budgets(self):
        if self.overlap_bytes >= self.chunk_bytes or self.input_bytes < self.chunk_bytes + 100:
            raise ValueError("invalid chunk or input budget")
        if self.lease_seconds <= self.timeout + 10:
            raise ValueError("lease must exceed embedding timeout")
        return self
