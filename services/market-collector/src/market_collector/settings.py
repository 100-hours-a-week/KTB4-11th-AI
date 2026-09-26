"""Configuration for the market-collector."""

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

    # The live path. The first two are the assumptions the design could not
    # measure -- Kiwoom's per-group symbol cap and whether one connection
    # carries several groups -- kept here so a measurement that contradicts
    # either is a configuration change and not a restructuring.
    ws_url: str = "wss://api.kiwoom.com:10000/api/dostk/websocket"
    ws_symbols_per_group: int = Field(default=100, gt=0)
    ws_groups_per_connection: int = Field(default=2, gt=0)
    ws_queue_size: int = Field(default=100_000, gt=0)
    live_flush_interval: float = Field(default=1.0, gt=0)
    live_window: int = Field(default=300, gt=0)

    @field_validator("backfill_depths", mode="after")
    @classmethod
    def _fill_missing_backfill_depths(cls, value: dict[str, int]) -> dict[str, int]:
        """Fill any timeframe an override omits from ``DEFAULT_DEPTHS``.

        ``__main__.run_backfill`` indexes ``backfill_depths[timeframe]`` for
        all four timeframes unconditionally. Before this validator, a partial
        override such as ``'{"1m": 120000}'`` — the obvious operator move to
        deepen just the minute walk — left the other three keys missing and
        crashed with ``KeyError: '15m'`` after the first symbol's 1m walk had
        already written and marked itself done. Lazily imported for the same
        reason ``_default_backfill_depths`` is: importing ``backfill`` at
        module load time would deadlock on the kiwoom.auth -> kiwoom.rest ->
        backfill -> settings import cycle.
        """
        from market_collector.backfill import DEFAULT_DEPTHS

        unknown = set(value) - set(DEFAULT_DEPTHS)
        if unknown:
            raise ValueError(
                f"unknown backfill timeframe(s) {sorted(unknown)}; "
                f"expected a subset of {sorted(DEFAULT_DEPTHS)}"
            )
        return {**DEFAULT_DEPTHS, **value}
