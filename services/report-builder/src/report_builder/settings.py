from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REPORT_BUILDER_", extra="ignore", hide_input_in_errors=True
    )

    postgres_dsn: str
    llm_api_key: SecretStr = Field(min_length=1)
    llm_model: str
    llm_timeout: float = Field(default=240, gt=0, le=240)
    backend_base_uri: str
    backend_jwt_secret: SecretStr = Field(min_length=32)
    backend_jwt_issuer: str
    backend_timeout: float = Field(default=10, gt=0, le=10)
    sqs_queue_url: str
    aws_region: str = "ap-northeast-2"
    log_level: str = "INFO"
