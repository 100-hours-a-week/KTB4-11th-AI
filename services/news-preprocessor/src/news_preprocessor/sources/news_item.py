"""The normalised records every publisher adapter emits."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FeedEntry:
    """One RSS item, before its article page is fetched."""

    source: str
    external_id: str
    url: str
    title: str
    published_at: datetime
    raw_payload: str


@dataclass(frozen=True)
class NewsItem(FeedEntry):
    """A feed entry with its scraped article body."""

    body: str
