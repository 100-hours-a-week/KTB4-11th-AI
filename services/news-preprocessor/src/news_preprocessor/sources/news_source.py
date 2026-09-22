"""The interface every publisher adapter implements."""

from typing import Protocol

from news_preprocessor.sources.news_item import FeedEntry, NewsItem


class NewsSource(Protocol):
    source: str

    def entries(self) -> list[FeedEntry]:
        """Fetch and parse the feed only; no article pages are requested."""
        ...

    def article(self, entry: FeedEntry) -> NewsItem:
        """Fetch the entry's page and extract its body. Raises EmptyBodyError."""
        ...
