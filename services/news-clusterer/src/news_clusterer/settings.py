from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_CLUSTERER_",
        extra="ignore",
    )

    log_level: str = "INFO"
    postgres_dsn: str
    llm_base_uri: str
    llm_model: str
    # Cosine distance lies in [0, 2].
    eps: float = Field(default=0.2, gt=0, le=2)
    min_samples: int = Field(default=3, gt=0)
    summary_max_chars: int = Field(default=24000, gt=0)
    llm_timeout: float = Field(default=120, gt=0)
