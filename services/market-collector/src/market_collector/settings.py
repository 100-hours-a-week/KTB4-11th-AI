from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class KiwoomAccount(BaseModel):
    app_key: str
    secret_key: str


def _default_backfill_depths() -> dict[str, int]:
    from market_collector.backfill import DEFAULT_DEPTHS

    return dict(DEFAULT_DEPTHS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MARKET_COLLECTOR_",
        extra="ignore",
    )

    log_level: str = "INFO"
    questdb_dsn: str
    questdb_ilp_host: str
    questdb_ilp_port: int = 9000
    kiwoom_accounts: list[KiwoomAccount] = Field(min_length=1)
    request_interval: float = 1.3
    theme_date_tps: list[int] = [5, 20, 60]
    index_code: str = "201"
    cursor_path: str = "var/market-collector/cursors.json"
    backfill_depths: dict[str, int] = Field(default_factory=_default_backfill_depths)
    indicators_on_backfill: bool = False

    ws_url: str = "wss://api.kiwoom.com:10000/api/dostk/websocket"
    ws_symbols_per_group: int = Field(default=100, gt=0)
    ws_groups_per_connection: int = Field(default=2, gt=0)
    ws_queue_size: int = Field(default=100_000, gt=0)
    live_flush_interval: float = Field(default=1.0, gt=0)
    live_window: int = Field(default=300, gt=0)
    intraday_timeframes: list[str] = ["15m", "1h"]

    @field_validator("intraday_timeframes", mode="after")
    @classmethod
    def _known_intraday_timeframes(cls, value: list[str]) -> list[str]:
        from market_collector.backfill import DEFAULT_DEPTHS

        unknown = [tf for tf in value if tf not in DEFAULT_DEPTHS]
        if unknown or not value:
            raise ValueError(
                f"intraday_timeframes must be a non-empty subset of "
                f"{sorted(DEFAULT_DEPTHS)}; got {value}"
            )
        return value

    @field_validator("backfill_depths", mode="after")
    @classmethod
    def _fill_missing_backfill_depths(cls, value: dict[str, int]) -> dict[str, int]:
        from market_collector.backfill import DEFAULT_DEPTHS

        unknown = set(value) - set(DEFAULT_DEPTHS)
        if unknown:
            raise ValueError(
                f"unknown backfill timeframe(s) {sorted(unknown)}; "
                f"expected a subset of {sorted(DEFAULT_DEPTHS)}"
            )
        return {**DEFAULT_DEPTHS, **value}
