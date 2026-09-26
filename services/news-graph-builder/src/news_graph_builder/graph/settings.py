from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_GRAPH_BUILDER_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    llm_base_uri: str
    llm_model: str
    llm_api_key: SecretStr | None = None
    summary_max_chars: int = Field(default=24000, gt=0)
    llm_timeout: float = Field(default=120, gt=0)
    max_entities: int = Field(default=30, gt=0)
    max_relations: int = Field(default=50, gt=0)
