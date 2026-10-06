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
    # Named like the other KTB_EMBEDDING_* variables, which describe the same endpoint.
    embedding_api_key: SecretStr | None = Field(
        default=None, validation_alias="KTB_EMBEDDING_API_KEY"
    )


class OCRSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEWS_PREPROCESSOR_OCR_", extra="ignore")

    enabled: bool = True
    languages: str = "kor+eng"
    max_images: int = Field(default=3, gt=0)
    max_image_bytes: int = Field(default=5_000_000, gt=0)
    max_image_pixels: int = Field(default=12_000_000, gt=0)
    download_timeout: float = Field(default=10, gt=0)
    timeout: float = Field(default=15, gt=0)
    min_characters: int = Field(default=20, gt=0)
    min_confidence: float = Field(default=60, ge=0, le=100)
