from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CompanySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_GRAPH_BUILDER_",
        extra="ignore",
        hide_input_in_errors=True,
    )

    kiwoom_app_key: SecretStr
    kiwoom_secret_key: SecretStr
    kiwoom_base_uri: str = "https://api.kiwoom.com"
    dart_api_key: SecretStr
