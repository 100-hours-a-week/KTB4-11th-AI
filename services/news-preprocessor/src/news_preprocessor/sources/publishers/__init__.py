import httpx

from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS
from news_preprocessor.sources.publishers.maeil import MaeilBusinessEconomyRSS

__all__ = ["HankyungEconomyRSS", "MaeilBusinessEconomyRSS", "publishers"]


def publishers(client: httpx.Client) -> tuple[NewsSource, ...]:
    return (HankyungEconomyRSS(client), MaeilBusinessEconomyRSS(client))
