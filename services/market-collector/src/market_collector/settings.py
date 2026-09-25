"""Configuration for the market-collector."""

from pydantic import BaseModel, Field
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
    cursor_path: str = "var/market-collector/cursors.json"
    backfill_depths: dict[str, int] = Field(default_factory=_default_backfill_depths)
    indicators_on_backfill: bool = False
