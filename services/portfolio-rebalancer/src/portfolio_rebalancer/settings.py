from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTFOLIO_REBALANCER_",
        extra="ignore",
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )

    postgres_dsn: str
    questdb_conf: str
    order_queue_url: str = Field(min_length=1)
    account_queue_url: str = Field(min_length=1)
    failure_queue_url: str = Field(min_length=1)
    drain_seconds: float = Field(default=30.0, gt=0)
    band: float = Field(default=0.05, gt=0, lt=1)
    buy_buffer: float = Field(default=0.02, ge=0, lt=1)
    test_mode: bool = False
    log_level: str = "INFO"
