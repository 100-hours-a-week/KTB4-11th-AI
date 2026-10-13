from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_REBALANCER_",
        populate_by_name=True,
        extra="ignore",
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )

    postgres_dsn: str
    questdb_conf: str
    backend_url: str = Field(min_length=1)
    backend_jwt_secret: SecretStr = Field(min_length=32)
    backend_jwt_issuer: str = Field(min_length=1)
    order_queue_url: str = Field(
        min_length=1,
        validation_alias=AliasChoices("ORDER_QUEUE_URL", "PORTFOLIO_REBALANCER_ORDER_QUEUE_URL"),
    )
    band: float = Field(default=0.05, gt=0, lt=1)
    buy_buffer: float = Field(default=0.02, ge=0, lt=1)
    test_mode: bool = False
    log_level: str = "INFO"

    @field_validator("order_queue_url")
    @classmethod
    def order_queue_is_fifo(cls, value: str) -> str:
        if not value.endswith(".fifo"):
            raise ValueError("order queue must be FIFO")
        return value
