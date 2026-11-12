from datetime import UTC, datetime

import questdb
from ktb_core.krx import KST
from ktb_core.logging import get_logger

from market_collector.kiwoom.official import ChartClient
from market_collector.kiwoom.parse import failure_fields, parse_field
from market_collector.settings import Settings
from market_collector.store import Store

log = get_logger(__name__)


PRICES = ("base_pric", "upl_pric", "lst_pric")


def stock_value(source: str, raw: object) -> int | None:
    text = str(raw).strip().replace(",", "")
    if not text or text == "-":
        if source in PRICES:
            raise ValueError(f"missing {source}")
        return None
    if source in ("eps", "bps") and "." in text:
        return None
    value = int(text)
    if source in PRICES:
        if value == 0:
            raise ValueError(f"invalid {source}: {text}")
        value = abs(value)
    return value


def stock_values(raw: dict[str, str]) -> dict[str, int | None]:
    return {
        target: parse_field(
            raw, source, lambda value, source=source: stock_value(source, value), api_id="ka10001"
        )
        for source, target in (
            ("base_pric", "base_price"),
            ("upl_pric", "upper_limit_price"),
            ("lst_pric", "lower_limit_price"),
            ("eps", "eps"),
            ("bps", "bps"),
        )
    }


def collect_corporations(
    settings: Settings, symbols: list[str], client: ChartClient, now: datetime
) -> None:
    day = now.astimezone(KST).date()
    succeeded = 0
    try:
        metadata = client.stock_metadata()
    except Exception:
        log.exception("stock_metadata_failed")
        metadata = {}
    with questdb.connect(settings.questdb_conf) as db:
        store = Store(db)
        for symbol in symbols:
            try:
                values: dict[str, int | str | None] = {**stock_values(client.stock_info(symbol))}
                names = metadata.get(symbol)
                if names:
                    values.update(stock_name=names[0], sector_name=names[1])
                store.write_corporation(symbol, day, datetime.now(UTC), values)
                succeeded += 1
            except Exception as error:
                log.exception(
                    "corporation_snapshot_failed",
                    **failure_fields(error),
                    symbol=symbol,
                    trade_date=day.isoformat(),
                )
    log.info(
        "corporation_snapshot_complete",
        succeeded=succeeded,
        symbols=len(symbols),
        trade_date=day.isoformat(),
        fetched_at=datetime.now(UTC).isoformat(),
    )
