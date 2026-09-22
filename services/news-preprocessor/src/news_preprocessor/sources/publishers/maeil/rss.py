import logging
import re
from collections.abc import Callable
from dataclasses import asdict
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, Tag
from ktb_core.utils import fetch

from news_preprocessor.sources import EmptyBodyError, FeedEntry, NewsItem
from news_preprocessor.sources.publishers.maeil.parser import parse_article_body

logger = logging.getLogger(__name__)

_COLON_OFFSET = re.compile(r"([+-]\d{2}):(\d{2})$")


class MaeilBusinessEconomyRSS:
    source = "maeil_business_economy"
    feed_url = "https://www.mk.co.kr/rss/30100041/"

    def __init__(self, fetch: Callable[[str, str], bytes] = fetch) -> None:
        self._fetch = fetch

    def entries(self) -> list[FeedEntry]:
        feed = BeautifulSoup(self._fetch(self.feed_url, "application/xml"), "xml")
        entries = []
        for item in feed.select("channel > item"):
            try:
                entries.append(self._entry(item))
            except ValueError as error:
                logger.warning("skipping %s feed item: %s", self.source, error)
        return entries

    def article(self, entry: FeedEntry) -> NewsItem:
        body = parse_article_body(self._fetch(entry.url, "text/html"))
        if not body:
            raise EmptyBodyError(entry.url)
        return NewsItem(**asdict(entry), body=body)

    def _entry(self, item: Tag) -> FeedEntry:
        link = _text(item, "link")
        title = _text(item, "title")
        pub_date = _text(item, "pubDate")
        if not (link and title and pub_date):
            raise ValueError(f"missing link, title or pubDate: {link or title!r}")
        # Maeil writes the offset as "+09:00"; parsedate_to_datetime then drops the timezone.
        published_at = parsedate_to_datetime(_COLON_OFFSET.sub(r"\1\2", pub_date))
        if published_at.tzinfo is None:
            raise ValueError(f"pubDate has no timezone: {pub_date!r}")
        return FeedEntry(
            source=self.source,
            external_id=link,
            url=link,
            title=title,
            published_at=published_at,
            raw_payload=str(item),
        )


def _text(item: Tag, name: str) -> str:
    element = item.find(name)
    return element.get_text(strip=True) if element else ""
