"""One subpackage per news outlet. Add an outlet by adding a subpackage and an entry here."""

from news_preprocessor.sources import NewsSource
from news_preprocessor.sources.publishers.hankyung import HankyungEconomyRSS

SOURCES: tuple[NewsSource, ...] = (HankyungEconomyRSS(),)

__all__ = ["SOURCES", "HankyungEconomyRSS"]
