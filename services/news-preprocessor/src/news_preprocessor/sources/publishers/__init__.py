import httpx

from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers.chosun import ChosunEconomyRSS
from news_preprocessor.sources.publishers.edaily import EdailyRSS
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS
from news_preprocessor.sources.publishers.maeil import MaeilBusinessEconomyRSS
from news_preprocessor.sources.publishers.opendart import OpenDart
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
    return (
        ChosunEconomyRSS(client),
        HankyungEconomyRSS(client),
        MaeilBusinessEconomyRSS(client),
        YonhapEconomyRSS(client),
        EdailyRSS(client),
        *(SeoulEconomicRSS(client, section) for section in SeoulEconomicRSS.sections),
        OpenDart(client, dart_api_key, stock_codes),
    )
