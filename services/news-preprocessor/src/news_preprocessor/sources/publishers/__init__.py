from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS
from news_preprocessor.sources.publishers.maeil import MaeilBusinessEconomyRSS

SOURCES: tuple[NewsSource, ...] = (HankyungEconomyRSS(), MaeilBusinessEconomyRSS())

__all__ = ["SOURCES", "HankyungEconomyRSS", "MaeilBusinessEconomyRSS"]
