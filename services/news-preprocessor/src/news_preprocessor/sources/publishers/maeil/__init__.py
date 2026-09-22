"""Maeil Business (매일경제) economy section."""

from news_preprocessor.sources.publishers.maeil.parser import MaeilBusinessEconomyParser
from news_preprocessor.sources.publishers.maeil.rss import MaeilBusinessEconomyRSS

__all__ = ["MaeilBusinessEconomyParser", "MaeilBusinessEconomyRSS"]
