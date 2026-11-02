from datetime import datetime

import httpx
from ktb_core.krx import KST

from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers.chosun import ChosunEconomyRSS
from news_preprocessor.sources.publishers.edaily import EdailyRSS
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS
from news_preprocessor.sources.publishers.maeil import MaeilBusinessEconomyRSS
from news_preprocessor.sources.publishers.opendart import OpenDart
from news_preprocessor.sources.publishers.opendart.settings import Settings as DartSettings
from news_preprocessor.sources.publishers.sedaily import SeoulEconomicRSS
from news_preprocessor.sources.publishers.yonhap import YonhapEconomyRSS

__all__ = [
    "ChosunEconomyRSS",
    "EdailyRSS",
    "HankyungEconomyRSS",
    "MaeilBusinessEconomyRSS",
    "OpenDart",
    "SeoulEconomicRSS",
    "YonhapEconomyRSS",
    "publishers",
]


def publishers(
    client: httpx.Client, dart_api_key: str, stock_codes: set[str]
) -> tuple[NewsSource, ...]:
    sources: tuple[NewsSource, ...] = (
        ChosunEconomyRSS(client),
        HankyungEconomyRSS(client),
        MaeilBusinessEconomyRSS(client),
        YonhapEconomyRSS(client),
        *(EdailyRSS(client, section) for section in EdailyRSS.sections),
        *(SeoulEconomicRSS(client, section) for section in SeoulEconomicRSS.sections),
    )
    settings = DartSettings()
    if settings.start_date is not None or datetime.now(KST).hour == settings.collection_hour:
        sources += (OpenDart(client, dart_api_key, stock_codes, settings=settings),)
    return sources
