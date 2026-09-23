import logging
from dataclasses import asdict
from email.utils import parsedate_to_datetime

import httpx
from bs4 import BeautifulSoup, Tag

from news_preprocessor.sources import EmptyBodyError, FeedEntry, NewsItem
from news_preprocessor.sources.publishers.hankyung.parser import parse_article_body

USER_AGENT = "ktb-ai/0.1"

logger = logging.getLogger(__name__)


class HankyungEconomyRSS:
    source = "hankyung_economy"
    feed_url = "https://www.hankyung.com/feed/economy"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(follow_redirects=True)

    def entries(self) -> list[FeedEntry]:
        feed = BeautifulSoup(self._get(self.feed_url, "application/xml"), "xml")
        entries = []
        for item in feed.select("channel > item"):
            try:
                entries.append(self._entry(item))
            except ValueError as error:
                logger.warning("skipping %s feed item: %s", self.source, error)
        return entries

    def article(self, entry: FeedEntry) -> NewsItem:
        body = parse_article_body(self._get(entry.url, "text/html"))
        if not body:
            raise EmptyBodyError(entry.url)
        return NewsItem(**asdict(entry), body=body)

    def _get(self, url: str, accept: str) -> str:
        response = self._client.get(
            url, headers={"User-Agent": USER_AGENT, "Accept": accept}, timeout=30
        )
        return response.raise_for_status().text

    def _entry(self, item: Tag) -> FeedEntry:
        link = _text(item, "link")
        title = _text(item, "title")
        pub_date = _text(item, "pubDate")
        if not (link and title and pub_date):
            raise ValueError(f"missing link, title or pubDate: {link or title!r}")
        published_at = parsedate_to_datetime(pub_date)
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
