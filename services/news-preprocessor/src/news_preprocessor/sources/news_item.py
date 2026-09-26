from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FeedEntry:
    source: str
    external_id: str
    url: str
    title: str
    published_at: datetime
    raw_payload: str


@dataclass(frozen=True)
class NewsItem(FeedEntry):
    body: str
