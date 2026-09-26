import httpx

from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS
from news_preprocessor.sources.publishers.maeil import MaeilBusinessEconomyRSS
from news_preprocessor.sources.publishers.yonhap import YonhapEconomyRSS

__all__ = ["HankyungEconomyRSS", "MaeilBusinessEconomyRSS", "YonhapEconomyRSS", "publishers"]


def publishers(client: httpx.Client) -> tuple[NewsSource, ...]:
    return (HankyungEconomyRSS(client), MaeilBusinessEconomyRSS(client), YonhapEconomyRSS(client))
