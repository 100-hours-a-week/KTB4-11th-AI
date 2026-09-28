from typing import Protocol

from news_preprocessor.sources.news_item import FeedEntry, NewsItem


class NewsSource(Protocol):
    source: str
    feed_url: str

    def entries(self) -> list[FeedEntry]: ...

    def article(self, entry: FeedEntry) -> NewsItem: ...
