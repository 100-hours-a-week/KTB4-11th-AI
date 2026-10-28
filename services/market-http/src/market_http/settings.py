from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_HTTP_", extra="ignore", hide_input_in_errors=True
    )

    questdb_conf: str
    postgres_dsn: str
    postgres_pool_size: int = Field(default=5, gt=0)
    postgres_timeout_seconds: int = Field(default=5, gt=0)
    valkey_host: str = Field(min_length=1)
    valkey_port: int = 6379
    valkey_max_connections: int | None = Field(default=None, gt=0)
    valkey_username: str | None = None
    valkey_cache_name: str | None = None
    valkey_tls: bool = False
    valkey_auth_mode: Literal["none", "iam"] = "none"
    task_id: str | None = None
    aws_region: str = "ap-northeast-2"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, gt=0, lt=65536)
    prometheus_bind_host: str = Field(default="127.0.0.1", min_length=1)
    prometheus_port: int = Field(default=9100, gt=0, lt=65536)
    cors_origins: list[str] = []
    cors_headers: list[str] = []
    docs_enabled: bool = True
    docs_path_prefix: str = Field(default="", pattern=r"^$|^/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
    log_level: str = "INFO"
    trace_spans_per_second: int = Field(default=100, ge=0)
    metrics_namespace: str = ""
    metrics_cluster_name: str = ""
    metrics_service_name: str = ""

    @model_validator(mode="after")
    def _metrics_need_dimensions(self) -> Self:
        if self.metrics_namespace and not (self.metrics_cluster_name and self.metrics_service_name):
            raise ValueError("metrics need metrics_cluster_name and metrics_service_name")
        return self

    @model_validator(mode="after")
    def _iam_needs_identity(self) -> Self:
        if self.valkey_auth_mode == "iam" and not (self.valkey_username and self.valkey_cache_name):
            raise ValueError("iam auth needs valkey_username and valkey_cache_name")
        return self
