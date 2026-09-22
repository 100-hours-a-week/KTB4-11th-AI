"""Maeil Business's economy RSS feed.

Items carry `no`, `title`, `link`, `category`, `author`, `pubDate`, `description` and
`media:content`, and no `guid`. `pubDate` writes its offset as `+09:00`, which is not
RFC 822: `parsedate_to_datetime` accepts it but returns a naive datetime, so the offset
is rewritten to `+0900` first.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import asdict
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from news_preprocessor.sources import (
    EmptyBodyError,
    FeedEntry,
    NewsItem,
    fetch_bytes,
    parse_article_text,
)
from news_preprocessor.sources.publishers.maeil.parser import MaeilBusinessEconomyParser

logger = logging.getLogger(__name__)

_COLON_OFFSET = re.compile(r"([+-]\d{2}):(\d{2})$")


class MaeilBusinessEconomyRSS:
    source = "maeil_business_economy"
    feed_url = "https://www.mk.co.kr/rss/30100041/"

    def __init__(self, fetch: Callable[[str], bytes] = fetch_bytes) -> None:
        self._fetch = fetch

    def entries(self) -> list[FeedEntry]:
        root = ElementTree.fromstring(self._fetch(self.feed_url))
        entries = []
        for item in root.findall("./channel/item"):
            try:
                entries.append(self._entry(item))
            except ValueError as error:
                logger.warning("skipping %s feed item: %s", self.source, error)
        return entries

    def article(self, entry: FeedEntry) -> NewsItem:
        body = parse_article_text(self._fetch(entry.url), MaeilBusinessEconomyParser())
        if not body:
            raise EmptyBodyError(entry.url)
        return NewsItem(**asdict(entry), body=body)

    def _entry(self, item: ElementTree.Element) -> FeedEntry:
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        if not (link and title and pub_date):
            raise ValueError(f"missing link, title or pubDate: {link or title!r}")
        published_at = parsedate_to_datetime(_COLON_OFFSET.sub(r"\1\2", pub_date))
        if published_at.tzinfo is None:
            raise ValueError(f"pubDate has no timezone: {pub_date!r}")
        return FeedEntry(
            source=self.source,
            external_id=link,
            url=link,
            title=title,
            published_at=published_at,
            raw_payload=ElementTree.tostring(item, encoding="unicode"),
        )
