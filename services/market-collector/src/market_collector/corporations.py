from datetime import UTC, datetime

import questdb
from ktb_core.krx import KST
from ktb_core.logging import get_logger

from market_collector.kiwoom.official import ChartClient
from market_collector.settings import Settings
from market_collector.store import Store

log = get_logger(__name__)


def stock_values(raw: dict[str, str]) -> dict[str, int | None]:
    values = {}
    for source, target in (
        ("base_pric", "base_price"),
        ("upl_pric", "upper_limit_price"),
        ("lst_pric", "lower_limit_price"),
        ("eps", "eps"),
        ("bps", "bps"),
    ):
        text = str(raw[source]).strip().replace(",", "")
        if not text or text == "-":
            if source in ("base_pric", "upl_pric", "lst_pric"):
                raise ValueError(f"missing {source}")
            values[target] = None
            continue
        if source in ("eps", "bps") and "." in text:
            values[target] = None
            continue
        value = int(text)
        if source in ("base_pric", "upl_pric", "lst_pric") and value <= 0:
            raise ValueError(f"invalid {source}: {text}")
        values[target] = value
    return values


def collect_corporations(
    settings: Settings, symbols: list[str], client: ChartClient, now: datetime
) -> None:
    day = now.astimezone(KST).date()
    succeeded = 0
    with questdb.connect(settings.questdb_conf) as db:
        store = Store(db)
        for symbol in symbols:
            try:
                values = stock_values(client.stock_info(symbol))
                store.write_corporation(symbol, day, datetime.now(UTC), values)
                succeeded += 1
            except Exception:
                log.exception(
                    "corporation_snapshot_failed", symbol=symbol, trade_date=day.isoformat()
                )
    log.info(
        "corporation_snapshot_complete",
        succeeded=succeeded,
        symbols=len(symbols),
        trade_date=day.isoformat(),
        fetched_at=datetime.now(UTC).isoformat(),
    )
