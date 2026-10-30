from typing import Literal

from ktb_core.embedding.config import embedding_space
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DISCLOSURE_RETRIEVAL_",
        extra="ignore",
        hide_input_in_errors=True,
        env_ignore_empty=True,
    )

    embedding_base_uri: str = Field(default="", validation_alias="DISCLOSURE_EMBEDDING_BASE_URI")
    embedding_model: str = Field(default="", validation_alias="DISCLOSURE_EMBEDDING_MODEL")
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
        default=2000, ge=1024, le=2000, validation_alias="DISCLOSURE_EMBEDDING_DIMENSIONS"
    )
    max_tokens: int = Field(
        default=4096, ge=1, le=16384, validation_alias="DISCLOSURE_EMBEDDING_MAX_TOKENS"
    )
    timeout: float = Field(default=30, gt=0, le=180)
    max_distance: float | None = Field(default=None, ge=0, le=2)

    inference_backend: Literal["openai", "modal"] = Field(
        default="openai", validation_alias="DISCLOSURE_INFERENCE_BACKEND"
    )
    modal_key: SecretStr | None = Field(default=None, validation_alias="DISCLOSURE_MODAL_KEY")
    modal_secret: SecretStr | None = Field(default=None, validation_alias="DISCLOSURE_MODAL_SECRET")
    modal_query_url: str = Field(default="", validation_alias="DISCLOSURE_MODAL_QUERY_URL")
    modal_rerank_url: str = Field(default="", validation_alias="DISCLOSURE_MODAL_RERANK_URL")

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
            if not self.modal_query_url.startswith("https://"):
                raise ValueError("Modal endpoint must use HTTPS")
            if not self.modal_rerank_url.startswith("https://"):
                raise ValueError("Modal endpoint must use HTTPS")
        elif self.dimensions != 2000 or not self.embedding_model or not self.embedding_base_uri:
            raise ValueError("Legacy embeddings require a model, base URI and 2000 dimensions")
        return self
