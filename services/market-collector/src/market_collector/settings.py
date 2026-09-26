from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class KiwoomAccount(BaseModel):
    app_key: str
    secret_key: str


def _default_backfill_depths() -> dict[str, int]:
    # Imported lazily, inside the factory rather than at module load time,
    # to break a real import cycle: kiwoom.auth imports Settings.KiwoomAccount,
    # kiwoom.rest imports kiwoom.auth, and backfill imports kiwoom.rest — so a
    # top-level `from market_collector.backfill import DEFAULT_DEPTHS` here
    # would deadlock the import machinery whenever backfill (or kiwoom.auth,
    # or kiwoom.rest) is the first of these modules imported in the process,
    # which routinely happens when a single test file is run directly.
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
    # 9000 is QuestDB's HTTP ILP port, which store.py's questdb_sink connects
    # to via Protocol.Http. 9009 is the TCP ILP port instead — a different
    # protocol on a different port — and compose.dev.yaml does not expose it.
    questdb_ilp_port: int = 9000
    kiwoom_accounts: list[KiwoomAccount] = Field(min_length=1)
    request_interval: float = 1.3
    theme_date_tps: list[int] = [5, 20, 60]
    # The KOSPI 200's Kiwoom sector code (ka20002's inds_cd). A setting, not
    # a constant, so a different index needs no code change -- see the
    # universe design's non-goals for why it is still a single value, not a
    # loop.
    index_code: str = "201"
    cursor_path: str = "var/market-collector/cursors.json"
    backfill_depths: dict[str, int] = Field(default_factory=_default_backfill_depths)
    indicators_on_backfill: bool = False

    # The live path. Measured against Kiwoom on 2026-09-26: one connection
    # accepted four groups, and a single group accepted 200 symbols, so both
    # figures below are conservative rather than binding. They stay settings
    # because a return_code=0 on an over-large registration cannot be told
    # from silent truncation until ticks actually flow -- 100 per group over
    # two groups is the shape that is both accepted and verifiable.
    ws_url: str = "wss://api.kiwoom.com:10000/api/dostk/websocket"
    ws_symbols_per_group: int = Field(default=100, gt=0)
    ws_groups_per_connection: int = Field(default=2, gt=0)
    ws_queue_size: int = Field(default=100_000, gt=0)
    live_flush_interval: float = Field(default=1.0, gt=0)
    live_window: int = Field(default=300, gt=0)
    # The timeframes the intraday refresh covers. Not "1m": the live path
    # produces those from ticks, and a REST sweep cannot keep up with them
    # anyway -- 200 symbols is 96-184 s per cycle across five accounts against
    # a 60-second budget.
    #
    # Whether a ka10080 page even carries the minute currently forming is
    # **unmeasured**: every page observed so far was fetched after the close.
    # If it does not, no number of accounts would make a REST sweep serve live
    # data. Worth measuring during market hours before anyone proposes one.
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
