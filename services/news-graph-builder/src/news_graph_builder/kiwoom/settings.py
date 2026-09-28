from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class KiwoomSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_GRAPH_BUILDER_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    kiwoom_app_key: SecretStr
    kiwoom_secret_key: SecretStr
    kiwoom_base_uri: str = "https://api.kiwoom.com"
    kiwoom_request_interval: float = Field(default=0.2, ge=0)
