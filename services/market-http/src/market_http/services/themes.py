import asyncio
from datetime import datetime

import sqlalchemy as sa
from ktb_core.krx import KST
from ktb_core.logging import get_logger
from redis.asyncio import Redis
from redis.exceptions import RedisError

from market_http.repositories.rankings import trade_values
from market_http.repositories.themes import (
    Theme,
    ThemeGroup,
    ThemeStock,
    find_theme_stocks,
    list_themes,
)

log = get_logger(__name__)


async def _trade_values(valkey: Redis, codes: list[str]) -> dict[str, float]:
    try:
        return await trade_values(valkey, list(dict.fromkeys(codes)), datetime.now(KST))
    except RedisError as error:
        log.warning("theme_trade_values_unavailable", error=str(error))
        return {}


def _by_member_trade_value(
    entries: list[tuple[Theme, list[str]]], values: dict[str, float]
) -> list[Theme]:
    ranked = sorted(
        entries,
        key=lambda entry: (
            -sum(values.get(code, 0) for code in entry[1]),
            -len(entry[1]),
            entry[0].code,
        ),
    )
    return [theme for theme, _ in ranked]


def _group_by_prefix(themes: list[Theme]) -> list[Theme | ThemeGroup]:
    result: list[Theme | ThemeGroup] = []
    groups: dict[str, ThemeGroup] = {}
    for theme in themes:
        prefix, separator, name = theme.name.partition("_")
        if not separator:
            result.append(theme)
            continue
        if prefix not in groups:
            groups[prefix] = ThemeGroup(name=prefix, items=[])
            result.append(groups[prefix])
        groups[prefix].items.append(Theme(code=theme.code, name=name))
    return result


async def get_themes(engine: sa.Engine, valkey: Redis) -> list[Theme | ThemeGroup]:
    entries = await asyncio.to_thread(list_themes, engine)
    values = await _trade_values(valkey, [code for _, members in entries for code in members])
    return _group_by_prefix(_by_member_trade_value(entries, values))


async def get_theme_stocks(
    engine: sa.Engine, valkey: Redis, theme_code: str
) -> list[ThemeStock] | None:
    stocks = await asyncio.to_thread(find_theme_stocks, engine, theme_code)
    if not stocks:
        return stocks
    values = await _trade_values(valkey, [stock.code for stock in stocks])
    return sorted(
        stocks,
        key=lambda stock: (
            stock.code not in values,
            -values.get(stock.code, 0),
            not stock.is_major,
            stock.code,
        ),
    )
